package term

import (
	"testing"
	"time"
)

type fakeTurns struct {
	idle, known bool
	running     string
	bgKnown     bool
	settled     bool
	session     *bool // nil: the hooks do not know
}

func (f fakeTurns) WaitIdle(string, time.Duration) (bool, bool) { return f.idle, f.known }
func (f fakeTurns) Background(string) (string, bool)            { return f.running, f.bgKnown }
func (f fakeTurns) Settled(string) bool                         { return f.settled }
func (f fakeTurns) Session(string) (bool, bool) {
	if f.session == nil {
		return false, false
	}
	return *f.session, true
}

func TestTurnsAnswerBeforeTheScreenAndTheScreenWhereTheyDoNotKnow(t *testing.T) {
	// The fake screen is always idle and shows no background hint.
	tm := newTerm(&fakeHost{screenAt: func(int) []string { return singleDefault }})
	tm.Turns = fakeTurns{idle: false, known: true, running: "2 background tasks", bgKnown: true}
	if tm.WaitIdle("%1", 10) || tm.IdleNow("%1") {
		t.Fatal("a turn the hooks say is open read as idle")
	}
	if got, ok := tm.BackgroundWork("%1"); got != "2 background tasks" || !ok || !tm.IdleWithBackground("%1") {
		t.Fatalf("background work: %q %v", got, ok)
	}
	tm.Turns = fakeTurns{}
	if !tm.WaitIdle("%1", 10) || !tm.IdleNow("%1") {
		t.Fatal("with nothing known the screen was not read")
	}
	if got, _ := tm.BackgroundWork("%1"); got != "" || tm.IdleWithBackground("%1") {
		t.Fatalf("with nothing known the screen was not read: %q", got)
	}
}

func TestASettledSessionIsEndedUnlessItsConversationMovedToTheBackground(t *testing.T) {
	// The screen shows a permission prompt, so a read refuses the close: only a settled turn gets past it.
	h := &hostBackend{live: map[string]bool{"%1": true}, screen: func(int) []string { return permissionPrompt }}
	tm := hostTerm(h)
	for _, turns := range []fakeTurns{{}, {idle: true, known: true}} {
		tm.Turns = turns
		if ok, why := tm.CloseSession("%1"); ok || len(h.respawn) != 0 {
			t.Fatalf("%+v: ended under a modal: %q", turns, why)
		}
	}
	tm.Turns = fakeTurns{idle: true, known: true, settled: true}
	if ok, why := tm.CloseSession("%1"); !ok || len(h.respawn) != 1 {
		t.Fatalf("a settled session was not ended: %q", why)
	}
	picker := []string{"Conversation moved to the background", rule, "│ > │", rule, "  enter to open"}
	h = &hostBackend{live: map[string]bool{"%1": true}, screen: func(int) []string { return picker }}
	tm = hostTerm(h)
	if b := tm.ScreenBlocker("%1"); b == nil || b.Kind != Backgrounded {
		t.Fatalf("the fixture is not the picker: %+v", b)
	}
	tm.Turns = fakeTurns{idle: true, known: true, settled: true}
	if ok, why := tm.CloseSession("%1"); ok || len(h.respawn) != 0 {
		t.Fatalf("ended under the session picker: %q", why)
	}
}

func TestTheHooksSayWhetherASessionIsThereBeforeTheScreenIsRead(t *testing.T) {
	reads := 0
	tm := newTerm(&fakeHost{screenAt: func(int) []string { reads++; return singleDefault }})
	for _, running := range []bool{true, false} {
		tm.Turns = fakeTurns{session: &running}
		if got, known := tm.SessionOnScreen("%1"); got != running || !known {
			t.Fatalf("hooks say %v: got %v known %v", running, got, known)
		}
	}
	if reads != 0 {
		t.Fatalf("the screen was read %d times though the hooks knew", reads)
	}
	tm.Turns = fakeTurns{}
	if got, known := tm.SessionOnScreen("%1"); !got || !known || reads == 0 {
		t.Fatalf("with nothing known the screen was not read: %v %v", got, known)
	}
}

func TestASettledTurnHasNoDialogUpWhateverItsScreenShows(t *testing.T) {
	reads := 0
	tm := newTerm(&fakeHost{screenAt: func(int) []string { reads++; return permissionPrompt }})
	tm.Turns = fakeTurns{idle: true, known: true, settled: true}
	if tm.DialogUp("%1") || reads != 0 {
		t.Fatalf("a settled turn read its screen %d times", reads)
	}
	for _, turns := range []Turns{nil, fakeTurns{idle: true, known: true}} {
		tm.Turns = turns
		if !tm.DialogUp("%1") {
			t.Fatalf("%+v: the prompt on the screen was not seen", turns)
		}
	}
}
