package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"

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
	needs := needsYou(home, d)
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
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated, account, worktree) VALUES('worker', 'RUNNING', 1, 'acct', ?)", wt); err != nil {
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
	if n := needsYou(home, d)("worker"); n != nil {
		t.Fatalf("a busy session needs nobody: %+v", n)
	}
	put("waiting", "permission prompt")
	n := needsYou(home, d)("worker")
	if n == nil || n.Kind != "permission" || n.Source != "session" || n.Text != "permission prompt" || n.Since != 5000 {
		t.Fatalf("%+v", n)
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
	needs := needsYou(home, d)
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
	d.Exec("INSERT INTO agent_state(agent, state, updated, account, worktree) VALUES('worker', 'RUNNING', 1, 'acct', ?)", wt)
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
	needs := needsYou(home, d)
	put("waiting", "permission prompt")
	if n := needs("worker"); n == nil || n.Source != "hook" {
		t.Fatalf("a session at its prompt: %+v", n)
	}
	put("busy", "")
	if n := needs("worker"); n != nil {
		t.Fatalf("a session that is busy again still needs someone: %+v", n)
	}
}
