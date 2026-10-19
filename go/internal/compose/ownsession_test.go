package compose

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/team"
)

// Two agents in one worktree on one account: each finds only the session running in its own pane, never the other's, however much newer
// that one is. The session a restart resumes is the one found here.
func TestAnAgentFindsOnlyTheSessionInItsOwnPane(t *testing.T) {
	p, home := rig(t)
	ft := newFakeTerminals()
	p.LC = &Lifecycle{term: ft.term()}
	child := exec.Command("sleep", "60")
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { child.Process.Kill(); child.Wait() })
	mine, theirs := child.Process.Pid, os.Getpid() // theirs is the test process, the parent of mine
	ft.panePid = map[string]int{"%1": mine, "%2": os.Getppid()}
	wt := t.TempDir()
	config, _ := team.AccountConfigDir(home, "acct")
	dir := filepath.Join(config, "sessions")
	os.MkdirAll(dir, 0o755)
	for _, s := range []struct {
		pid     int
		sid     string
		updated int
	}{{mine, "alice-session", 1}, {theirs, "bob-session", 9}} {
		os.WriteFile(filepath.Join(dir, fmt.Sprintf("%d.json", s.pid)),
			[]byte(fmt.Sprintf(`{"pid":%d,"sessionId":%q,"cwd":%q,"status":"idle","updatedAt":%d}`, s.pid, s.sid, wt, s.updated)), 0o644)
	}
	p.db.Exec("INSERT INTO agent_state(agent, state, updated, worktree, account, terminal) VALUES('alice', 'IDLE', 1, ?, 'acct', '%1'), "+
		"('bob', 'IDLE', 1, ?, 'acct', '%2')", wt, wt)
	if rec, _, found := p.terminalSession("alice", nil); found != sessionFound || rec.SessionID != "alice-session" {
		t.Fatalf("alice found %q (%v): another agent's session, sharing her worktree", rec.SessionID, found)
	}
	if rec, _, found := p.terminalSession("bob", nil); found != sessionFound || rec.SessionID != "bob-session" {
		t.Fatalf("bob found %q (%v)", rec.SessionID, found)
	}
	// Whose a session is cannot be told (the host names no pane process): in a shared worktree that is unknown, never the newest there.
	ft.panePid = map[string]int{}
	if rec, _, found := p.terminalSession("alice", nil); found != sessionUnknown {
		t.Fatalf("with no pane to tell by, alice found %q (%v)", rec.SessionID, found)
	}
	// Alone in its worktree, the worktree still decides.
	p.db.Exec("UPDATE agent_state SET worktree = ? WHERE agent = 'bob'", t.TempDir())
	if rec, _, found := p.terminalSession("alice", nil); found != sessionFound || rec.SessionID != "bob-session" {
		t.Fatalf("alone in its worktree: %q (%v)", rec.SessionID, found)
	}
}
