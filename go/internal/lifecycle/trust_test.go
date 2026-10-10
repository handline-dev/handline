package lifecycle

import (
	"errors"
	"github.com/handline/handline/go/internal/term"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// screenTerm is a terminal showing the vendor's startup screens, recording what is pressed.
type screenTerm struct {
	fakeTerm
	bypass, trust bool
	folder        string
	standsAfter   bool // a press leaves the screen standing
	presses       []string
	blocker       *term.Blocker // another screen a launch stops at
}

func (s *screenTerm) BypassPromptShowing(string) bool    { return s.bypass }
func (s *screenTerm) ScreenBlocker(string) *term.Blocker { return s.blocker }
func (s *screenTerm) TrustPromptShowing(string) bool     { return s.trust }
func (s *screenTerm) TrustPromptFolder(string) string    { return s.folder }
func (s *screenTerm) WaitForTrustPrompt(string, bool)    {}
func (s *screenTerm) ConfirmTrust(string) (bool, string) {
	s.presses = append(s.presses, "trust")
	s.trust = s.standsAfter
	return true, ""
}
func (s *screenTerm) AcceptBypass(string) (bool, string) {
	s.presses = append(s.presses, "bypass")
	s.bypass = s.standsAfter
	return true, ""
}

type trustAgents struct {
	fakeAgents
	skip      bool
	skipErr   error
	declared  bool
	decisions []string
}

func (a *trustAgents) SkipPermissionsInEffect(string) (bool, error) { return a.skip, a.skipErr }
func (a *trustAgents) IsDeclared(string) bool                       { return a.declared }
func (a *trustAgents) VendorName(string) string                     { return "claude" }
func (a *trustAgents) RaiseDecision(_ db.Q, _, text, _ string) error {
	a.decisions = append(a.decisions, text)
	return nil
}

type trustRig struct {
	m  *Manager
	tm *screenTerm
	ag *trustAgents
}

// trustManager is an agent "a" with a recorded worktree dir, in front of a trust screen naming the same folder, with consent given.
func trustManager(t *testing.T) *trustRig {
	t.Helper()
	m, _, _ := newManager(t)
	dir := t.TempDir()
	tm := &screenTerm{trust: true, folder: dir}
	ag := &trustAgents{declared: true}
	m.Term, m.Agents = tm, ag
	m.Blocked = blockedWith{}
	if err := db.SetTerminal(m.Q, "a", "t1", dir); err != nil {
		t.Fatal(err)
	}
	return &trustRig{m, tm, ag}
}

type blockedWith struct{}

func (blockedWith) NoteCloseFailure(db.Q, string)          {}
func (blockedWith) PastDialog(db.Q, string) (bool, string) { return false, "" }
func (blockedWith) StopWorkAndExit(db.Q, string) bool      { return false }

func TestTrustIsPressedOnlyWithConsentFolderAndRecordedWorktreeAgreeing(t *testing.T) {
	r := trustManager(t)
	if ok, why := r.m.clearTrustPrompt("a", "t1", false); !ok || why != "clear" || len(r.tm.presses) != 1 || len(r.ag.decisions) != 0 {
		t.Fatalf("%v %q %v %v", ok, why, r.tm.presses, r.ag.decisions)
	}
	cases := map[string]func(*trustRig){
		"folder undeclared":   func(r *trustRig) { r.ag.declared = false },
		"folder not recorded": func(r *trustRig) { r.tm.folder = t.TempDir() },
		"folder unreadable":   func(r *trustRig) { r.tm.folder = "" },
	}
	for name, unset := range cases {
		r := trustManager(t)
		unset(r)
		ok, why := r.m.clearTrustPrompt("a", "t1", false)
		if ok || len(r.tm.presses) != 0 || len(r.ag.decisions) != 1 || !strings.Contains(why, TrustStall) {
			t.Errorf("%s: %v %q presses=%v decisions=%v", name, ok, why, r.tm.presses, r.ag.decisions)
		}
		row, _ := db.GetAgentState(r.m.Q, "a")
		if row.State != states.Stalled {
			t.Errorf("%s: state %q", name, row.State)
		}
		// The same screen again raises nothing more.
		if ok, _ := r.m.clearTrustPrompt("a", "t1", false); ok || len(r.ag.decisions) != 1 {
			t.Errorf("%s: raised again: %v", name, r.ag.decisions)
		}
	}
}

func TestAPressThatLeavesTheTrustScreenStandingRaisesTheDecision(t *testing.T) {
	r := trustManager(t)
	r.tm.standsAfter = true
	ok, why := r.m.clearTrustPrompt("a", "t1", false)
	if ok || len(r.tm.presses) != 1 || len(r.ag.decisions) != 1 || !strings.Contains(why, TrustStall) {
		t.Fatalf("%v %q %v %v", ok, why, r.tm.presses, r.ag.decisions)
	}
}

func TestBypassWarningIsAcceptedOnlyWhereSkippingPermissionsIsInEffect(t *testing.T) {
	cases := []struct {
		name    string
		skip    bool
		err     error
		pressed int
		want    string
	}{
		{"granted", true, nil, 1, "accepted"},
		{"ungranted", false, nil, 0, "not granted"},
		{"unreadable agent.yaml", false, errors.New("bad yaml"), 0, "could not be read"},
	}
	for _, c := range cases {
		r := trustManager(t)
		r.tm.trust, r.tm.bypass = false, true
		r.ag.skip, r.ag.skipErr = c.skip, c.err
		ok, why := r.m.clearTrustPrompt("a", "t1", false)
		// After an accepted warning the screen is clear, so the whole check ends clear.
		if len(r.tm.presses) != c.pressed || (c.pressed == 1) != ok || (c.pressed == 0 && !strings.Contains(why, c.want)) {
			t.Errorf("%s: %v %q presses=%v", c.name, ok, why, r.tm.presses)
		}
	}
}

// A launch that stops at a dialog the table does not give the daemon to answer presses nothing: it stalls and the founder is asked.
func TestAStartupDialogNoTableRowAnswersIsLeftToThePerson(t *testing.T) {
	r := trustManager(t)
	r.tm.trust = false
	r.tm.blocker = &term.Blocker{Kind: term.Modal, Title: "New MCP servers found"}
	ok, why := r.m.clearTrustPrompt("a", "t1", true)
	if ok || len(r.tm.presses) != 0 || len(r.ag.decisions) != 1 || !strings.Contains(why, DialogStall) {
		t.Fatalf("%v %q presses=%v decisions=%v", ok, why, r.tm.presses, r.ag.decisions)
	}
}
