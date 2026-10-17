package lifecycle

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/term"
)

// screenTerm is a terminal showing the vendor's startup screens. It has no way to press anything: nothing on these screens is answered.
type screenTerm struct {
	fakeTerm
	trust   bool
	folder  string
	blocker *term.Blocker // another screen a launch stops at
}

func (s *screenTerm) ScreenBlocker(string) *term.Blocker { return s.blocker }
func (s *screenTerm) TrustPromptShowing(string) bool     { return s.trust }
func (s *screenTerm) TrustPromptFolder(string) string    { return s.folder }

type trustAgents struct {
	fakeAgents
	decisions []string
}

func (a *trustAgents) VendorName(string) string { return "claude" }
func (a *trustAgents) RaiseDecision(_ db.Q, _, text, _ string) error {
	a.decisions = append(a.decisions, text)
	return nil
}

type trustRig struct {
	m    *Manager
	tm   *screenTerm
	ag   *trustAgents
	logs []string
}

// trustManager is an agent "a" with a recorded worktree dir, in front of a trust screen naming the same folder.
func trustManager(t *testing.T) *trustRig {
	t.Helper()
	m, _, _ := newManager(t)
	dir := t.TempDir()
	r := &trustRig{m: m, tm: &screenTerm{trust: true, folder: dir}, ag: &trustAgents{}}
	m.Term, m.Agents = r.tm, r.ag
	m.Log = func(f string, a ...any) { r.logs = append(r.logs, f) }
	m.Blocked = blockedWith{}
	if err := db.SetTerminal(m.Q, "a", "t1", dir); err != nil {
		t.Fatal(err)
	}
	return r
}

type blockedWith struct{}

func (blockedWith) NoteCloseFailure(db.Q, string)          {}
func (blockedWith) PastDialog(db.Q, string) (bool, string) { return false, "" }
func (blockedWith) StopWorkAndExit(db.Q, string) bool      { return false }

func TestNothingShowingIsClear(t *testing.T) {
	r := trustManager(t)
	r.tm.trust = false
	if ok, why := r.m.startupScreens("a", "t1"); !ok || why != "clear" || len(r.ag.decisions) != 0 {
		t.Fatalf("%v %q %v", ok, why, r.ag.decisions)
	}
}

// The launch marked the worktree trusted, so a trust screen is a fault: it is logged and raised once, and nothing is pressed.
func TestATrustScreenAfterTheLaunchIsLoggedAndRaisedOnce(t *testing.T) {
	r := trustManager(t)
	ok, why := r.m.startupScreens("a", "t1")
	if ok || len(r.ag.decisions) != 1 || !strings.Contains(why, TrustStall) {
		t.Fatalf("%v %q %v", ok, why, r.ag.decisions)
	}
	if len(r.logs) == 0 || !strings.Contains(r.logs[0], "folder-trust dialog showed") {
		t.Fatalf("logs %v", r.logs)
	}
	if row, _ := db.GetAgentState(r.m.Q, "a"); row.State != states.Stalled {
		t.Fatalf("state %q", row.State)
	}
	if ok, _ := r.m.startupScreens("a", "t1"); ok || len(r.ag.decisions) != 1 {
		t.Fatalf("raised again: %v", r.ag.decisions)
	}
}

// A launch that stops at a dialog the table does not give the daemon to answer presses nothing: it stalls and the founder is asked.
func TestAStartupDialogNoTableRowAnswersIsLeftToThePerson(t *testing.T) {
	r := trustManager(t)
	r.tm.trust = false
	r.tm.blocker = &term.Blocker{Kind: term.Modal, Title: "New MCP servers found"}
	ok, why := r.m.startupScreens("a", "t1")
	if ok || len(r.ag.decisions) != 1 || !strings.Contains(why, DialogStall) {
		t.Fatalf("%v %q decisions=%v", ok, why, r.ag.decisions)
	}
}
