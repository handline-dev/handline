package term

import (
	"testing"
	"time"
)

type fakeTurns struct {
	idle, known bool
	running     string
	bgKnown     bool
}

func (f fakeTurns) WaitIdle(string, time.Duration) (bool, bool) { return f.idle, f.known }
func (f fakeTurns) Background(string) (string, bool)            { return f.running, f.bgKnown }

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
