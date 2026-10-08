package tick

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

func setState(t *testing.T, tk *Tick, agent, state string, wakeAt int64) {
	t.Helper()
	if err := db.SetAgentState(tk.Q, agent, state, wakeAt, ""); err != nil {
		t.Fatal(err)
	}
}

func supervise(tk *Tick, agent string) []string {
	var actions []string
	tk.Supervise(agent, 10_000, map[string][]Sample{}, &actions)
	return actions
}

func addRequest(t *testing.T, tk *Tick, target string) {
	t.Helper()
	if _, err := db.AddRequest(tk.Q, db.NewRequest{Requester: "human", Target: target, Body: "x", Response: "async", Kind: "ask"}); err != nil {
		t.Fatal(err)
	}
}

func TestADueParkWakesTheAgentAndSaysWhatItBecame(t *testing.T) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Parked, 9_000)
	w.wakeOK = true
	actions := supervise(tk, "a")
	if !w.did("wake a") || !has(actions, "PARKED -> IDLE (nothing queued)") {
		t.Fatalf("%v %v", w.calls, actions)
	}
	row, _ := db.GetAgentState(tk.Q, "a")
	if row.State != states.Idle {
		t.Fatalf("%+v", row)
	}
	// With a request waiting the same wake leaves it QUEUED: the window rolled over for work.
	tk, w = newWorld(t)
	setState(t, tk, "a", states.Throttled, 9_000)
	addRequest(t, tk, "a")
	w.wakeOK = true
	if actions := supervise(tk, "a"); !has(actions, "THROTTLED -> QUEUED (window rolled over)") {
		t.Fatalf("%v", actions)
	}
}

func TestADeadlineNotYetDueDoesNotWake(t *testing.T) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Parked, 20_000)
	supervise(tk, "a")
	if w.did("wake") {
		t.Fatalf("woke before its deadline: %v", w.calls)
	}
}

func TestAClosedAgentWhoseDeadlineIsSpentWithNothingWaitingStaysClosed(t *testing.T) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Parked, 9_000)
	if err := db.SetClosed(tk.Q, "a", "s1"); err != nil {
		t.Fatal(err)
	}
	w.alive["a"], w.waiting, w.resumable = false, false, true
	actions := supervise(tk, "a")
	if w.did("wake") {
		t.Fatalf("a closed session was resumed to be told nothing: %v", w.calls)
	}
	// The park is spent: it reads IDLE, and what arrives next is what wakes it.
	if !has(actions, "PARKED -> IDLE") {
		t.Fatalf("%v", actions)
	}
}

func TestAClosedAgentWhoseWakeFailsStaysDueAndTheFailureIsNoted(t *testing.T) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Parked, 9_000)
	db.SetClosed(tk.Q, "a", "s1")
	w.alive["a"], w.waiting, w.resumable, w.wakeOK, w.wakeWhy = false, true, true, false, "no room"
	supervise(tk, "a")
	if !w.did(`failure "could not be woken - no room"`) || !w.logged("stays PARKED, still due") {
		t.Fatalf("%v %v", w.calls, w.logs)
	}
	row, _ := db.GetAgentState(tk.Q, "a")
	if row.State != states.Parked || row.WakeAt.Int64 != 9_000 {
		t.Fatalf("the deadline was cleared by a wake that did not work: %+v", row)
	}
}

func TestWorkWaitingForAnAgentWithNoSessionStartsItAndSaysSoOncePerReason(t *testing.T) {
	tk, w := newWorld(t)
	w.alive["a"] = false
	w.need = &Need{Kind: "start", What: "work waiting"}
	w.startSaid = "a: not started - no room"
	actions := supervise(tk, "a")
	supervise(tk, "a")
	if !w.did("start a") || !has(actions, "a: not started - no room") || !w.did(`failure "could not be started - a: not started - no room"`) {
		t.Fatalf("%v %v", w.calls, actions)
	}
	n := 0
	for _, l := range w.logs {
		if strings.Contains(l, "could not be started") {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("a refusal that lasts for hours was said %d times", n)
	}
	// A start that leaves a session running clears the failure.
	tk, w = newWorld(t)
	w.alive["a"], w.need, w.startAlive, w.startSaid = false, &Need{Kind: "start", What: "work waiting"}, true, "a: started"
	supervise(tk, "a")
	if !w.did(`failure ""`) {
		t.Fatalf("%v", w.calls)
	}
}

func TestWorkWaitingForAClosedAgentWakesIt(t *testing.T) {
	tk, w := newWorld(t)
	w.need = &Need{Kind: "wake", What: "a request"}
	w.wakeOK = true
	if actions := supervise(tk, "a"); !has(actions, "a: woken for a request") {
		t.Fatalf("%v", actions)
	}
	tk, w = newWorld(t)
	w.need = &Need{Kind: "wake", What: "a request"}
	w.wakeWhy = "the session would not start"
	actions := supervise(tk, "a")
	if !has(actions, "a: could not wake - the session would not start") || !w.did(`failure "could not be woken - the session would not start"`) {
		t.Fatalf("%v %v", actions, w.calls)
	}
}

func TestAnIdleOpenSessionIsClosedUnlessItsNoteIsBeingWrittenFor(t *testing.T) {
	tk, w := newWorld(t)
	w.shouldClose, w.closeOK = true, true
	if actions := supervise(tk, "a"); !w.did("close a") || !has(actions, "a: session closed while idle") {
		t.Fatalf("%v %v", w.calls, actions)
	}
	tk, w = newWorld(t)
	w.shouldClose, w.hold = true, true
	supervise(tk, "a")
	if w.did("close") {
		t.Fatalf("closed while the close was held for the note: %v", w.calls)
	}
	// A refusal is said once per reason.
	tk, w = newWorld(t)
	w.shouldClose, w.closeWhy = true, "mid-turn"
	supervise(tk, "a")
	supervise(tk, "a")
	n := 0
	for _, l := range w.logs {
		if strings.Contains(l, "idle but not closed - mid-turn") {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("said %d times", n)
	}
}

func TestAnAgentAtTheWallIsNotClosedOrSentWork(t *testing.T) {
	tk, w := newWorld(t)
	w.shouldClose, w.closeOK = true, true
	w.atLimit = &Limit{Text: "5-hour limit reached", Source: "StopFailure"}
	supervise(tk, "a")
	if w.did("close") || w.did("queued") || w.did("answers") {
		t.Fatalf("an agent at the wall was sent work or closed: %v", w.calls)
	}
}

func TestAUsageParkEndsTheMomentTheReadingNoLongerFiresAndNotOnAGuess(t *testing.T) {
	setup := func(source string) (*Tick, *world) {
		tk, w := newWorld(t)
		setState(t, tk, "a", states.Parked, 99_999) // not yet due: only the un-park can end it
		if err := db.StartHandoff(tk.Q, "h1", "a", "", "usage", 99_999); err != nil {
			t.Fatal(err)
		}
		if err := db.AdvanceHandoff(tk.Q, "h1", "done", "done", ""); err != nil {
			t.Fatal(err)
		}
		w.reading = &AccountReading{Account: "acct", Source: source}
		w.movable["usage"] = true
		pct := 40
		w.status = Status{ShortTrusted: &pct}
		return tk, w
	}
	tk, w := setup("recorded")
	actions := supervise(tk, "a")
	if !has(actions, "PARKED -> IDLE (usage recovered)") {
		t.Fatalf("%v %v", actions, w.calls)
	}
	// An account that is only assumed is not evidence enough to wake the agent into.
	tk, _ = setup("assumed")
	if actions := supervise(tk, "a"); has(actions, "usage recovered") {
		t.Fatalf("%v", actions)
	}
	// Still at the wall: it stays parked.
	tk, w = setup("recorded")
	wall := 100
	w.status.ShortTrusted = &wall
	if actions := supervise(tk, "a"); has(actions, "usage recovered") {
		t.Fatalf("%v", actions)
	}
}

func TestAParkedAgentAtTheWallWithRoomElsewhereMovesOffItsAccount(t *testing.T) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Parked, 99_999)
	db.StartHandoff(tk.Q, "h1", "a", "", "usage", 99_999)
	db.AdvanceHandoff(tk.Q, "h1", "done", "done", "")
	w.reading = &AccountReading{Account: "acct", Source: "recorded"}
	w.movable["usage"] = true
	w.movePlan, w.moved = "other", true
	wall := 100
	w.status = Status{ShortTrusted: &wall}
	if actions := supervise(tk, "a"); !has(actions, "a: moved off a parked account") || !w.did("moveroom") {
		t.Fatalf("%v %v", actions, w.calls)
	}
}

func TestAFiredTriggerHandsTheAgentOffAndDegradedOnesCountTowardsAStall(t *testing.T) {
	tk, w := newWorld(t)
	over := 250_000
	w.status = Status{TrustedCtxUsed: &over}
	w.performOK = true
	actions := supervise(tk, "a")
	if !w.did("handoff a context") || !has(actions, "a: handoff (context)") || has(actions, "degraded") {
		t.Fatalf("%v %v", w.calls, actions)
	}
	// A handoff that degraded says so, and the streak that reaches the limit stalls the agent for a human.
	tk, w = newWorld(t)
	w.status = Status{TrustedCtxUsed: &over}
	w.performOK = false
	if err := db.BumpCounter(tk.Q, "a", "throttle_count", states.MaxThrottleStreak); err != nil {
		t.Fatal(err)
	}
	actions = supervise(tk, "a")
	if !has(actions, "a: handoff (context) -> degraded") || !has(actions, "STALLED after 5 degraded handoffs") {
		t.Fatalf("%v", actions)
	}
	row, _ := db.GetAgentState(tk.Q, "a")
	if row.State != states.Stalled {
		t.Fatalf("%+v", row)
	}
}

func TestATurnThatEndedOnTheUsageLimitIsHandedOffEvenWhenTheReadingLags(t *testing.T) {
	tk, w := newWorld(t)
	w.atLimit = &Limit{Text: "You've hit your weekly limit", Source: "StopFailure"}
	pct := 10
	w.status = Status{ShortTrusted: &pct}
	w.performOK = true
	supervise(tk, "a")
	if !w.did("handoff a usage_7d") {
		t.Fatalf("%v", w.calls)
	}
}

func TestAnAgentMidHandoffAndAnUnrelatedTriggerlessAgentAreLeftAlone(t *testing.T) {
	tk, w := newWorld(t)
	over := 250_000
	w.status = Status{TrustedCtxUsed: &over}
	db.StartHandoff(tk.Q, "h1", "a", "", "context", 99_999)
	supervise(tk, "a")
	if len(w.calls) != 0 {
		t.Fatalf("an agent mid-handoff is excluded from trigger evaluation: %v", w.calls)
	}
	tk, w = newWorld(t)
	w.triggers = false // a closed agent's other end is a shell prompt
	w.status = Status{TrustedCtxUsed: &over}
	supervise(tk, "a")
	if w.did("handoff") {
		t.Fatalf("%v", w.calls)
	}
}
