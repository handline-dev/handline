package session

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/term"
)

type acceptFixture struct {
	*fixture
	cfg   *fakeConfig
	rules fakeRules
}

func newAcceptFixture(t *testing.T) *acceptFixture {
	f := newFixture(t)
	a := &acceptFixture{fixture: f, cfg: &fakeConfig{map[string]bool{}}, rules: fakeRules{}}
	f.Config, f.Rules = a.cfg, a.rules
	f.Agents = func() []string { return []string{"worker"} }
	f.agent("worker", Running, "h1")
	return a
}

func (a *acceptFixture) events(kind string) []string {
	rows, err := a.DB.Query("SELECT COALESCE(payload, '') FROM events WHERE kind = ? ORDER BY id", kind)
	if err != nil {
		a.t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var p string
		rows.Scan(&p)
		out = append(out, p)
	}
	return out
}

func (a *acceptFixture) sweep() {
	a.t.Helper()
	if err := a.SweepAutoAccept(); err != nil {
		a.t.Fatal(err)
	}
}

func TestTheSettingDefaultsOnAndRoundTrips(t *testing.T) {
	a := newAcceptFixture(t)
	if !a.AutoAcceptEnabled() {
		t.Fatal("off by default")
	}
	a.SetAutoAccept(false)
	if a.AutoAcceptEnabled() || a.cfg.flags[AutoAcceptKey] {
		t.Fatal("not turned off")
	}
	a.SetAutoAccept(true)
	if !a.AutoAcceptEnabled() {
		t.Fatal("not turned on")
	}
}

func (a *acceptFixture) waitFor(tool string) {
	id := a.event("worker", "PermissionRequest")
	a.NoteWait("worker", tool, map[string]any{"command": "x"}, &id)
	a.term.blockers["h1"] = &term.Blocker{Kind: term.Modal, Title: "Run it?"}
}

func TestAStuckPromptIsReportedAndNotPressedEveryTick(t *testing.T) {
	a := newAcceptFixture(t)
	a.waitFor("Browser")
	a.term.accepts["Browser"] = true
	a.term.press["h1"] = [3]string{term.StillOpen, "Run it?", "still"}
	a.sweep()
	if got := a.events("dialog_accept_failed"); len(got) != 1 || !strings.Contains(got[0], `"tool": "Browser"`) {
		t.Fatalf("failed events %v", got)
	}
	if !strings.Contains(a.logs[0], `pressed Enter on "Run it?" (Browser) and it is still open`) {
		t.Errorf("log %q", a.logs)
	}
	a.now += AcceptStuckBackoffS - 1
	a.sweep()
	if len(a.term.pressed) != 1 {
		t.Fatalf("pressed again inside the backoff: %v", a.term.pressed)
	}
	a.now++
	a.sweep()
	if len(a.term.pressed) != 2 {
		t.Fatalf("not pressed once the backoff is over: %v", a.term.pressed)
	}
}

func TestAnAcceptedPromptIsOnTheRecord(t *testing.T) {
	a := newAcceptFixture(t)
	a.waitFor("Browser")
	a.term.accepts["Browser"] = true
	a.term.press["h1"] = [3]string{term.Cleared, "Run it?", ""}
	a.sweep()
	if got := a.events("dialog_accepted"); len(got) != 1 || !strings.Contains(got[0], `"title": "Run it?"`) {
		t.Fatalf("accepted %v", got)
	}
	if !strings.Contains(a.logs[0], "said yes once to Browser (auto-accept)") {
		t.Errorf("log %q", a.logs)
	}
}

func TestNothingIsAcceptedWhenTheSettingIsOffTheToolIsNotTaken(t *testing.T) {
	a := newAcceptFixture(t)
	a.waitFor("Browser")
	a.term.press["h1"] = [3]string{term.Cleared, "Run it?", ""}
	a.sweep() // the vendor takes no tool
	a.term.accepts["Browser"] = true
	a.cfg.flags[AutoAcceptKey] = false
	a.sweep()
	a.cfg.flags[AutoAcceptKey] = true
	a.exec("UPDATE agent_state SET state = 'IDLE'")
	a.sweep()
	a.exec("UPDATE agent_state SET state = 'RUNNING', terminal = NULL")
	a.sweep()
	if len(a.term.pressed) != 0 {
		t.Fatalf("pressed %v", a.term.pressed)
	}
}

func TestAPromptNothingPressedOnFallsThroughToTheRules(t *testing.T) {
	a := newAcceptFixture(t)
	a.waitFor("Browser")
	a.term.accepts["Browser"] = true
	a.term.press["h1"] = [3]string{term.NotPressed, "", term.NoDialog}
	a.rules[[2]string{"claude", "Run it?"}] = "Yes, always"
	a.term.dialogs["h1"] = &term.Dialog{Title: "Run it?", Options: []term.Option{{Number: 1, Label: "Yes"}, {Number: 2, Label: "Yes, always"}}}
	a.sweep()
	if strings.Join(a.term.pressed, ",") != "accept Browser:h1,answer:h1" {
		t.Errorf("pressed %v", a.term.pressed)
	}
}

func TestADialogARuleNamesIsAnsweredAndRecordedWithItsLabel(t *testing.T) {
	a := newAcceptFixture(t)
	a.exec("UPDATE agent_state SET state = 'IDLE'")
	a.term.blockers["h1"] = exitDialogBlocker()
	a.term.dialogs["h1"] = &term.Dialog{Title: "Background work is running", Options: []term.Option{{Number: 1, Label: "Exit and stop tasks"}, {Number: 2, Label: "Stay"}}}
	a.rules[[2]string{"claude", "Background work is running"}] = "Exit and stop tasks"
	a.term.press["h1"] = [3]string{term.Cleared, "Background work is running", ""}
	a.sweep()
	got := a.events("dialog_accepted")
	if len(got) != 1 || !strings.Contains(got[0], `"label": "Exit and stop tasks"`) || !strings.Contains(got[0], `"by": "rule"`) ||
		!strings.Contains(got[0], `"title": "Background work is running"`) {
		t.Fatalf("accepted %v", got)
	}
	if !strings.Contains(a.logs[0], `answered "Background work is running" with 'Exit and stop tasks' (rule)`) {
		t.Errorf("log %q", a.logs)
	}
}

func TestADialogNoRuleCoversIsLeftAloneAndCostsNoRead(t *testing.T) {
	a := newAcceptFixture(t)
	a.term.blockers["h1"] = exitDialogBlocker()
	a.sweep() // no rule at all: no screen is read
	if len(a.term.cachedNB) != 0 || len(a.term.pressed) != 0 {
		t.Fatalf("read %d pressed %v with no rule", len(a.term.cachedNB), a.term.pressed)
	}
	a.rules[[2]string{"claude", "Another title"}] = "x"
	a.sweep()
	if len(a.term.cachedNB) == 0 || len(a.term.pressed) != 0 {
		t.Fatalf("a rule for another title: read %d pressed %v", len(a.term.cachedNB), a.term.pressed)
	}
	a.rules[[2]string{"opencode", "Background work is running"}] = "x" // another vendor's rule
	a.sweep()
	a.term.blockers["h1"] = &term.Blocker{Kind: term.Backgrounded, Title: "Another title"}
	a.sweep()
	a.term.blockers["h1"] = nil
	a.sweep()
	if len(a.term.pressed) != 0 {
		t.Fatalf("pressed %v", a.term.pressed)
	}
}

func TestARulePressThatDoesNotCloseTheDialogIsNotRepeated(t *testing.T) {
	a := newAcceptFixture(t)
	a.term.blockers["h1"] = exitDialogBlocker()
	a.term.dialogs["h1"] = &term.Dialog{Title: "Background work is running", Options: []term.Option{{Number: 1, Label: "Exit and stop tasks"}, {Number: 2, Label: "Stay"}}}
	a.rules[[2]string{"claude", "Background work is running"}] = "Stay"
	a.term.press["h1"] = [3]string{term.StillOpen, "Background work is running", "still"}
	a.sweep()
	a.sweep()
	if len(a.term.pressed) != 1 {
		t.Fatalf("pressed %v", a.term.pressed)
	}
	if got := a.events("dialog_accept_failed"); len(got) != 1 || !strings.Contains(got[0], `"label": "Stay"`) {
		t.Errorf("failed %v", got)
	}
}

func TestAnswerByRuleIsForTheAgentsOwnTerminal(t *testing.T) {
	a := newAcceptFixture(t)
	a.term.blockers["h1"] = exitDialogBlocker()
	a.term.dialogs["h1"] = &term.Dialog{Title: "Background work is running", Options: []term.Option{{Number: 1, Label: "Exit and stop tasks"}, {Number: 2, Label: "Stay"}}}
	if ok, err := a.AnswerByRule("worker", "h1"); ok || err != nil {
		t.Fatalf("with no rule: %v %v", ok, err)
	}
	a.rules[[2]string{"claude", "Background work is running"}] = "Exit and stop tasks"
	a.term.press["h1"] = [3]string{term.Cleared, "Background work is running", ""}
	if ok, _ := a.AnswerByRule("worker", "other-handle"); ok {
		t.Fatal("answered a terminal that is not the agent's")
	}
	if ok, _ := a.AnswerByRule("nobody", "h1"); ok {
		t.Fatal("answered for an agent with no row")
	}
	if ok, err := a.AnswerByRule("worker", "h1"); !ok || err != nil {
		t.Fatalf("not answered: %v %v", ok, err)
	}
}
