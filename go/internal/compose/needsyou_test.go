package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/team"
)

func TestNeedsYouComesFromTheWaitFileAndTheSessionFileAndStopsWhenTheTurnMovedOn(t *testing.T) {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	needs := (&Parts{db: d, home: home}).needsYou()
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated) VALUES('worker', 'RUNNING', 1)"); err != nil {
		t.Fatal(err)
	}
	if needs("worker") != nil {
		t.Fatal("an agent with nothing waiting needs nobody")
	}
	write := func(m map[string]any) {
		b, _ := json.Marshal(m)
		os.MkdirAll(filepath.Join(home, "waiting"), 0o755)
		os.WriteFile(filepath.Join(home, "waiting", "worker.json"), b, 0o644)
	}
	write(map[string]any{"tool": "Bash", "command": "rm -rf build", "since": 1700, "after_event_id": 5})
	n := needs("worker")
	if n == nil || n.Kind != "permission" || n.Source != "hook" || n.Text != "Bash: rm -rf build" || n.Since != 1700 {
		t.Fatalf("%+v", n)
	}
	write(map[string]any{"tool": "AskUserQuestion", "command": "which one", "since": 1700})
	if n := needs("worker"); n == nil || n.Kind != "question" {
		t.Fatalf("a question put to the person: %+v", n)
	}
	// The agent reported after the prompt (event 9 > 5): it was answered, and nobody is needed.
	write(map[string]any{"tool": "Bash", "command": "x", "since": 1700, "after_event_id": 5})
	if _, err := d.Exec("INSERT INTO events(id, ts, agent, kind) VALUES(9, 1701, 'worker', 'report')"); err != nil {
		t.Fatal(err)
	}
	if n := needs("worker"); n != nil {
		t.Fatalf("a turn that went on past the prompt still needs someone: %+v", n)
	}
}

func TestASessionFileSayingWaitingNeedsAPersonAndOneSayingBusyDoesNot(t *testing.T) {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	config, err := team.AccountConfigDir(home, "acct")
	if err != nil {
		t.Fatal(err)
	}
	wt := t.TempDir()
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated, account, worktree, terminal) VALUES('worker', 'RUNNING', 1, 'acct', ?, '%1')", wt); err != nil {
		t.Fatal(err)
	}
	pid := os.Getpid() // a live process
	put := func(status, waitingFor string) {
		dir := filepath.Join(config, "sessions")
		os.MkdirAll(dir, 0o755)
		rec, _ := json.Marshal(map[string]any{"pid": pid, "sessionId": "s", "cwd": wt, "version": "2.1.296", "peerProtocol": 1,
			"messagingSocketPath": "/tmp/x.sock", "status": status, "waitingFor": waitingFor, "updatedAt": 5000000})
		os.WriteFile(filepath.Join(dir, strconv.Itoa(pid)+".json"), rec, 0o644)
		os.WriteFile(filepath.Join(dir, strconv.Itoa(pid)+".k.key"), []byte("k"), 0o600)
	}
	put("busy", "")
	if n := (&Parts{db: d, home: home}).needsYou()("worker"); n != nil {
		t.Fatalf("a busy session needs nobody: %+v", n)
	}
	put("waiting", "permission prompt")
	n := (&Parts{db: d, home: home}).needsYou()("worker")
	if n == nil || n.Kind != "permission" || n.Source != "session" || n.Text != "permission prompt" || n.Since != 5000 {
		t.Fatalf("%+v", n)
	}
}

// Claude Code writes waiting with `<Tool>: <input>` while a command runs, with nothing drawn: only a hook event for the prompt makes it a card.
func TestARunningCommandInTheSessionFileIsNotADialog(t *testing.T) {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	config, err := team.AccountConfigDir(home, "acct")
	if err != nil {
		t.Fatal(err)
	}
	wt := t.TempDir()
	d.Exec("INSERT INTO agent_state(agent, state, updated, account, worktree, terminal) VALUES('worker', 'RUNNING', 1, 'acct', ?, '%1')", wt)
	put := func(waitingFor string) {
		dir := filepath.Join(config, "sessions")
		os.MkdirAll(dir, 0o755)
		pid := os.Getpid()
		rec, _ := json.Marshal(map[string]any{"pid": pid, "sessionId": "s", "cwd": wt, "version": "2.1.296", "peerProtocol": 1,
			"messagingSocketPath": "/tmp/x.sock", "status": "waiting", "waitingFor": waitingFor, "updatedAt": 5000000})
		os.WriteFile(filepath.Join(dir, strconv.Itoa(pid)+".json"), rec, 0o644)
		os.WriteFile(filepath.Join(dir, strconv.Itoa(pid)+".k.key"), []byte("k"), 0o600)
	}
	needs := (&Parts{db: d, home: home}).needsYou()
	for _, w := range []string{"Bash: cd /Users/ron/work && make permission", "Bash: cd /w", "something new", ""} {
		put(w)
		if n := needs("worker"); n != nil {
			t.Errorf("%q with no hook event raised a card: %+v", w, n)
		}
	}
	for w, kind := range map[string]string{"question": "question", "usage limit": "usage", "permission prompt": "permission"} {
		put(w)
		if n := needs("worker"); n == nil || n.Kind != kind {
			t.Errorf("%q: %+v, want %s", w, n, kind)
		}
	}
	put("Bash: cd /w")
	os.MkdirAll(filepath.Join(home, "waiting"), 0o755)
	os.WriteFile(filepath.Join(home, "waiting", "worker.json"), []byte(`{"tool":"Bash","command":"cd /w","since":1700}`), 0o644)
	if n := needs("worker"); n == nil || n.Kind != "permission" || n.Source != "hook" {
		t.Fatalf("the same call with a PermissionRequest: %+v", n)
	}
}

// A wait file outlives a session that died at its prompt; it counts only for a row that is running and open.
func TestAWaitFileDoesNotOutliveItsSession(t *testing.T) {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	os.MkdirAll(filepath.Join(home, "waiting"), 0o755)
	os.WriteFile(filepath.Join(home, "waiting", "worker.json"), []byte(`{"tool":"Bash","command":"rm -rf build","since":1700}`), 0o644)
	needs := (&Parts{db: d, home: home}).needsYou()
	if needs("worker") != nil {
		t.Fatal("no row: nobody is waiting")
	}
	for name, row := range map[string]string{
		"closed":  "INSERT INTO agent_state(agent, state, updated, closed_at) VALUES('worker', 'IDLE', 1, 2000)",
		"parked":  "INSERT INTO agent_state(agent, state, updated, wake_at) VALUES('worker', 'PARKED', 1, 9000)",
		"stalled": "INSERT INTO agent_state(agent, state, updated) VALUES('worker', 'STALLED', 1)",
	} {
		d.Exec("DELETE FROM agent_state")
		if _, err := d.Exec(row); err != nil {
			t.Fatal(err)
		}
		if n := needs("worker"); n != nil {
			t.Errorf("%s: a row that is not running still needs someone: %+v", name, n)
		}
	}
	d.Exec("DELETE FROM agent_state")
	d.Exec("INSERT INTO agent_state(agent, state, updated) VALUES('worker', 'RUNNING', 1)")
	if needs("worker") == nil {
		t.Fatal("a running row at its prompt")
	}
}

// A prompt the person answered stays in the wait file while its tool runs; the session says busy, and that ends the need.
func TestALiveSessionThatIsBusyEndsTheNeedTheWaitFileStillSays(t *testing.T) {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	config, err := team.AccountConfigDir(home, "acct")
	if err != nil {
		t.Fatal(err)
	}
	wt := t.TempDir()
	d.Exec("INSERT INTO agent_state(agent, state, updated, account, worktree, terminal) VALUES('worker', 'RUNNING', 1, 'acct', ?, '%1')", wt)
	os.MkdirAll(filepath.Join(home, "waiting"), 0o755)
	os.WriteFile(filepath.Join(home, "waiting", "worker.json"), []byte(`{"tool":"Bash","command":"tools/suite.sh","since":1700}`), 0o644)
	put := func(status, waitingFor string) {
		dir := filepath.Join(config, "sessions")
		os.MkdirAll(dir, 0o755)
		pid := os.Getpid()
		rec, _ := json.Marshal(map[string]any{"pid": pid, "sessionId": "s", "cwd": wt, "version": "2.1.296", "peerProtocol": 1,
			"messagingSocketPath": "/tmp/x.sock", "status": status, "waitingFor": waitingFor, "updatedAt": 5000000})
		os.WriteFile(filepath.Join(dir, strconv.Itoa(pid)+".json"), rec, 0o644)
		os.WriteFile(filepath.Join(dir, strconv.Itoa(pid)+".k.key"), []byte("k"), 0o600)
	}
	needs := (&Parts{db: d, home: home}).needsYou()
	put("waiting", "permission prompt")
	if n := needs("worker"); n == nil || n.Source != "hook" {
		t.Fatalf("a session at its prompt: %+v", n)
	}
	put("busy", "")
	if n := needs("worker"); n != nil {
		t.Fatalf("a session that is busy again still needs someone: %+v", n)
	}
}

// A row's worktree may be recorded as `<repo>::<path>`; the session is still found by the path.
func TestNeedsYouFindsTheSessionOfAWorktreeRecordedAsRepoAndPath(t *testing.T) {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	config, err := team.AccountConfigDir(home, "acct")
	if err != nil {
		t.Fatal(err)
	}
	wt := t.TempDir()
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated, account, worktree, terminal) VALUES('worker', 'RUNNING', 1, 'acct', ?, '%1')", "repo-id::"+wt); err != nil {
		t.Fatal(err)
	}
	os.MkdirAll(filepath.Join(home, "waiting"), 0o755)
	os.WriteFile(filepath.Join(home, "waiting", "worker.json"), []byte(`{"tool":"Bash","command":"tools/suite.sh","since":1700}`), 0o644)
	put := func(status, waitingFor string) {
		dir := filepath.Join(config, "sessions")
		os.MkdirAll(dir, 0o755)
		pid := os.Getpid()
		rec, _ := json.Marshal(map[string]any{"pid": pid, "sessionId": "s", "cwd": wt, "version": "2.1.296", "peerProtocol": 1,
			"messagingSocketPath": "/tmp/x.sock", "status": status, "waitingFor": waitingFor, "updatedAt": 5000000})
		os.WriteFile(filepath.Join(dir, strconv.Itoa(pid)+".json"), rec, 0o644)
		os.WriteFile(filepath.Join(dir, strconv.Itoa(pid)+".k.key"), []byte("k"), 0o600)
	}
	needs := (&Parts{db: d, home: home}).needsYou()
	put("busy", "")
	if n := needs("worker"); n != nil {
		t.Fatalf("a live session that is not waiting clears the stale wait file: %+v", n)
	}
	os.Remove(filepath.Join(home, "waiting", "worker.json"))
	put("waiting", "permission prompt")
	if n := needs("worker"); n == nil || n.Source != "session" {
		t.Fatalf("a session waiting is reported: %+v", n)
	}
}

func TestAPromptTheNotificationHookReportsNeedsAPersonOnlyBehindTheSwitch(t *testing.T) {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated) VALUES('worker', 'RUNNING', 1)"); err != nil {
		t.Fatal(err)
	}
	if err := hooks.Apply(d, home, "worker", "Notification", map[string]any{"notification_type": "permission_prompt"}, "n1", 1700); err != nil {
		t.Fatal(err)
	}
	if n := (&Parts{db: d, home: home}).needsYou()("worker"); n != nil {
		t.Fatalf("the switch is off, and the notification raised a card: %+v", n)
	}
	needs := (&Parts{db: d, home: home, notified: true}).needsYou()
	n := needs("worker")
	if n == nil || n.Kind != "permission" || n.Source != "notification" || n.Since != 1700 || n.Text != "handle this in the terminal" {
		t.Fatalf("%+v", n)
	}
	os.MkdirAll(filepath.Join(home, "waiting"), 0o755)
	os.WriteFile(filepath.Join(home, "waiting", "worker.json"), []byte(`{"tool": "Bash", "command": "make", "since": 1690}`), 0o644)
	if n := needs("worker"); n == nil || n.Source != "notification" {
		t.Fatalf("the permission hook's card stands over a prompt nobody answered: %+v", n)
	}
	os.WriteFile(filepath.Join(home, "waiting", "worker.json"), []byte(`{"tool": "AskUserQuestion", "since": 1690}`), 0o644)
	if _, err := d.Exec("UPDATE agent_state SET state = 'IDLE'"); err != nil {
		t.Fatal(err)
	}
	if n := needs("worker"); n != nil {
		t.Fatalf("a question is left to its own card: %+v", n)
	}
	os.Remove(filepath.Join(home, "waiting", "worker.json"))
	if err := hooks.Apply(d, home, "worker", "PostToolUse", map[string]any{"tool_name": "Bash"}, "n2", 1710); err != nil {
		t.Fatal(err)
	}
	if n := needs("worker"); n != nil {
		t.Fatalf("the tool ran, and the card is still up: %+v", n)
	}
}

// A dialog stall or an agent the healer gave up on is one waiting-agent card from the daemon's own record, and it goes with its cause.
func TestADialogStallAndAnUnhealedAgentAreOneCardEachAndGoWhenTheirCauseDoes(t *testing.T) {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	needs := (&Parts{db: d, home: home}).needsYou()
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, detail, updated) VALUES('worker', 'IDLE', '', 1)"); err != nil {
		t.Fatal(err)
	}
	if needs("worker") != nil {
		t.Fatal("nothing waits")
	}
	set := func(state, detail string) {
		if _, err := d.Exec("UPDATE agent_state SET state = ?, detail = ?, updated = 1700 WHERE agent = 'worker'", state, detail); err != nil {
			t.Fatal(err)
		}
	}
	set("STALLED", "worker's terminal is showing a "+lifecycle.DialogStall+" only a person answers")
	if n := needs("worker"); n == nil || n.Kind != "dialog" || n.Source != "daemon" || n.Since != 1700 {
		t.Fatalf("a dialog stall is a dialog card: %+v", n)
	}
	set("IDLE", "the dialog is answered; the stall is lifted")
	if needs("worker") != nil {
		t.Fatal("the card goes when the dialog closes")
	}
	// The healer's ladder at its last rung, then the trouble ending.
	for k, v := range map[string]int64{"heal:worker": 4, "heal_at:worker": 1800} {
		if err := db.SetCursor(d, k, v); err != nil {
			t.Fatal(err)
		}
	}
	if n := needs("worker"); n == nil || n.Kind != "stuck" || n.Since != 1800 {
		t.Fatalf("stuck past every step: %+v", n)
	}
	if err := db.SetCursor(d, "heal_quiet:worker", 1900); err != nil {
		t.Fatal(err)
	}
	if needs("worker") != nil {
		t.Fatal("the card goes when the trouble does")
	}
}
