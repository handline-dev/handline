package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

// termTickRig is bob, a terminal agent in worktree wt, pane %1, recorded on account a1.
func termTickRig(t *testing.T) (*Parts, *fakeTerminals, string) {
	t.Helper()
	ft := newFakeTerminals()
	p, _, _, _ := wiredWith(t, ft)
	wt, _ := filepath.EvalSymlinks(t.TempDir())
	if err := dbpkg.SetAccount(p.Sink.DB, "bob", "a1", "recorded"); err != nil {
		t.Fatal(err)
	}
	if err := dbpkg.SetTerminal(p.Sink.DB, "bob", "%1", wt); err != nil {
		t.Fatal(err)
	}
	ft.live["%1"] = true
	return p, ft, wt
}

// writeSession is the file a running claude keeps for its process under an account's config directory.
func writeSession(t *testing.T, p *Parts, acct string, pid int, body string) {
	t.Helper()
	dir, _ := team.AccountConfigDir(p.home, acct)
	os.MkdirAll(filepath.Join(dir, "sessions"), 0o755)
	if err := os.WriteFile(filepath.Join(dir, "sessions", strconv.Itoa(pid)+".json"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestTheTickReadsATerminalAgentsSessionFromItsSessionFile(t *testing.T) {
	p, ft, wt := termTickRig(t)
	host := tickHost{p: p, terminals: true}
	if host.SessionAlive("bob") {
		t.Fatal("no session file, no session")
	}
	writeSession(t, p, "a2", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"s1","status":"busy"}`)
	if !host.SessionAlive("bob") || host.SessionIdle("bob") {
		t.Fatal("a session mid-turn is alive and not idle")
	}
	if got := host.RunningAccount("bob"); got != "a2" {
		t.Fatalf("the account is the one whose directory holds the file, not the recorded one: %q", got)
	}
	writeSession(t, p, "a2", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"s1","status":"waiting","waitingFor":"permission prompt"}`)
	if host.SessionIdle("bob") {
		t.Fatal("a session at a permission prompt is not idle: a restart would cut it short")
	}
	writeSession(t, p, "a2", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"s1","status":"idle"}`)
	if !host.SessionIdle("bob") {
		t.Fatal("idle")
	}
	delete(ft.live, "%1")
	if host.SessionAlive("bob") {
		t.Fatal("a file whose pane is not listed is a stranger's")
	}
	ft.live["%1"] = true
	writeSession(t, p, "a2", 1<<30, `{"cwd":"`+wt+`","sessionId":"s1","status":"idle"}`)
	os.Remove(filepath.Join(p.home, "accounts", "a2", ".claude", "sessions", strconv.Itoa(os.Getpid())+".json"))
	if host.SessionAlive("bob") {
		t.Fatal("a file left by a process that died is not a session")
	}
}

func TestTerminalAgentsAreOnlyTickedWhenAskedFor(t *testing.T) {
	p, _, _ := termTickRig(t)
	has := func(list []string, a string) bool {
		return strings.Contains(" "+strings.Join(list, " ")+" ", " "+a+" ")
	}
	if has(tickHost{p: p}.Agents(), "bob") || !has(tickHost{p: p}.Agents(), "alice") {
		t.Fatalf("structured only: %v", tickHost{p: p}.Agents())
	}
	if !has(tickHost{p: p, terminals: true}.Agents(), "bob") {
		t.Fatal("with terminals bob is ticked")
	}
}

func TestTheReconcilerRecordsATerminalAgentWhoseSessionEndedAsClosedWithItsSession(t *testing.T) {
	p, _, wt := termTickRig(t)
	d := p.Sink.DB
	dbpkg.SetResumeSession(d, "bob", "22222222-2222-4222-8222-222222222222")
	tk := p.Tick(p.Run.cfg, TickConfig{Terminals: true})
	// the pane is listed and no session runs in it: the session ended without us
	actions := []string{}
	tk.Tick.Reconcile.Sweep(d, &actions)
	row, _ := dbpkg.GetAgentState(d, "bob")
	if !row.ClosedAt.Valid || row.ClosedAt.Int64 == 0 || row.ResumeSession.String != "22222222-2222-4222-8222-222222222222" {
		t.Fatalf("closed with its session kept: %+v %v", row, actions)
	}
	// a session runs again (a human relaunched it): the closed mark was wrong
	writeSession(t, p, "a1", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"s2","status":"idle"}`)
	tk.Tick.Reconcile.Sweep(d, &actions)
	if row, _ = dbpkg.GetAgentState(d, "bob"); row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 {
		t.Fatalf("a running session clears the closed mark: %v", actions)
	}
}

func TestATerminalAgentWithNoStatuslineHasItsSessionAndContextFromItsFileAndTranscript(t *testing.T) {
	p, _, wt := termTickRig(t)
	sid := "33333333-3333-4333-8333-333333333333"
	writeSession(t, p, "a1", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"`+sid+`","status":"idle"}`)
	dir, _ := team.AccountConfigDir(p.home, "a1")
	os.MkdirAll(filepath.Join(dir, "projects", "x"), 0o755)
	os.WriteFile(filepath.Join(dir, "projects", "x", sid+".jsonl"), []byte(
		`{"type":"assistant","message":{"model":"claude-opus-5-1","usage":{"input_tokens":10,"cache_read_input_tokens":150000,"cache_creation_input_tokens":5}}}`+"\n"), 0o644)
	tk := p.Tick(p.Run.cfg, TickConfig{Terminals: true})
	st := tk.Tick.Usage.ReadForAgent("bob", "a1")
	if st.SessionID != sid || st.TrustedCtxUsed == nil || *st.TrustedCtxUsed != 150015 {
		t.Fatalf("%+v", st)
	}
}

func TestASessionFileThatCannotBeReadUnderALivePidIsUnknownNotAbsent(t *testing.T) {
	for name, body := range map[string]string{"empty": "", "invalid json": `{"cwd": "`} {
		t.Run(name, func(t *testing.T) {
			p, _, _ := termTickRig(t)
			d := p.Sink.DB
			writeSession(t, p, "a1", os.Getpid(), body) // being written as the tick looks
			host := tickHost{p: p, terminals: true}
			if !host.SessionAlive("bob") || host.SessionIdle("bob") {
				t.Fatal("a process runs and what it says cannot be read: alive, not idle")
			}
			tk := p.Tick(p.Run.cfg, TickConfig{Terminals: true})
			actions := []string{}
			tk.Tick.Reconcile.Sweep(d, &actions)
			if row, _ := dbpkg.GetAgentState(d, "bob"); row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 {
				t.Fatalf("a live agent was recorded closed: %v", actions)
			}
		})
	}
}

func TestOfSeveralSessionFilesInTheWorktreeTheNewestByUpdatedAtIsTheSession(t *testing.T) {
	p, _, wt := termTickRig(t)
	writeSession(t, p, "a1", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"old","status":"idle","updatedAt":100}`)
	writeSession(t, p, "a1", os.Getppid(), `{"cwd":"`+wt+`","sessionId":"new","status":"busy","updatedAt":200}`)
	rec, _, found := p.terminalSession("bob", map[string]bool{"%1": true})
	if found != sessionFound || rec.SessionID != "new" {
		t.Fatalf("%v %+v", found, rec)
	}
}

func TestAHandoffReadsATerminalAgentsSessionAndItsPidFromTheSessionFile(t *testing.T) {
	p, _, wt := termTickRig(t)
	run := termRun{p}
	if pid, status := run.Session("bob"); pid != 0 || status != "" {
		t.Fatalf("no file, no session: %d %q", pid, status)
	}
	writeSession(t, p, "a1", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"s1","status":"idle"}`)
	if pid, status := run.Session("bob"); pid != os.Getpid() || status != "idle" {
		t.Fatalf("%d %q", pid, status)
	}
	writeSession(t, p, "a1", os.Getpid(), `{"cwd": "`)
	if pid, status := run.Session("bob"); pid != 0 || status != "unknown" {
		t.Fatalf("a file that cannot be read has no pid to prove an end by: %d %q", pid, status)
	}
	if run.PidGone(os.Getpid()) || !run.PidGone(1<<30) {
		t.Fatal("a pid is gone when no process has it")
	}
}

// A terminal agent has an agent view row when the daemon ticks terminals: its session file says whether it runs and is busy, and a session
// at a prompt shows needs_you.
func TestATerminalAgentsViewRowSaysWhatItsSessionFileSays(t *testing.T) {
	p, _, wt := termTickRig(t)
	if err := dbpkg.SetAgentState(p.Sink.DB, "bob", "IDLE", 0, ""); err != nil { // a row that has not caught up with a turn that began
		t.Fatal(err)
	}
	tu := tickUsage{p: p, u: p.LC.usage, roster: p.LC.roster}
	stored := func(av interface{ RefreshOne(string) }, want string) map[string]any {
		t.Helper()
		av.RefreshOne("bob")
		var raw string
		if err := p.Sink.DB.QueryRow("SELECT view FROM agent_view WHERE agent = 'bob'").Scan(&raw); err != nil {
			t.Fatalf("no row: %v", err)
		}
		var v map[string]any
		json.Unmarshal([]byte(raw), &v)
		if v["state"] != want {
			t.Fatalf("state %v, want %s: %v", v["state"], want, v)
		}
		return v
	}
	// Not ticking terminals: the Python daemon's row stands, nothing is written.
	p.agentView(p.Run.cfg, true, false, tu).RefreshOne("bob")
	var n int
	p.Sink.DB.QueryRow("SELECT COUNT(*) FROM agent_view WHERE agent = 'bob'").Scan(&n)
	if n != 0 {
		t.Fatal("a terminal agent's row was written by a daemon that does not tick terminals")
	}
	av := p.agentView(p.Run.cfg, true, true, tu)
	writeSession(t, p, "a1", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"s1","status":"busy"}`)
	if v := stored(av, "running"); v["needs_you"] != nil { // busy session: the turn is running whatever the row says
		t.Fatal(v)
	}
	if err := dbpkg.SetAgentState(p.Sink.DB, "bob", "RUNNING", 0, ""); err != nil { // the prompt arrives mid-turn: the row stays running
		t.Fatal(err)
	}
	writeSession(t, p, "a1", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"s1","status":"waiting","waitingFor":"permission prompt","updatedAt":5000000}`)
	v := stored(av, "waiting")
	ny, _ := v["needs_you"].(map[string]any)
	if v["has_dialog"] != true || ny["kind"] != "permission" || ny["source"] != "session" {
		t.Fatal(v)
	}
	writeSession(t, p, "a1", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"s1","status":"idle"}`)
	if err := dbpkg.SetAgentState(p.Sink.DB, "bob", "IDLE", 0, ""); err != nil { // the turn ended: the Stop hook moved the row
		t.Fatal(err)
	}
	if v := stored(av, "idle"); v["needs_you"] != nil || v["has_dialog"] != false {
		t.Fatal(v)
	}
}
