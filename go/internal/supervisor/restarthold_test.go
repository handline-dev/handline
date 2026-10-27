package supervisor

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func (r *rig) decideHandoff(agent string) {
	r.t.Helper()
	r.must(db.StartHandoff(r.DB, "h-"+agent, agent, "s1", "context", r.now()+90))
}

// A message sent between the handoff decision and the close is not typed at the old session: it waits, and the new session is told it first,
// with the line that says it arrived during the restart.
func TestAMessageBetweenTheHandoffDecisionAndTheCloseGoesToTheNewSession(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.decideHandoff("bob")
	rid := r.founderSays("bob", "are you there?")
	if got, _ := r.TellNow(r.DB, rid); !strings.Contains(got, "held while it restarts") {
		t.Fatalf("told at once: %q", got)
	}
	if ok, msg, _ := r.SendMessage(r.DB, "bob", "a bare line"); !ok || strings.Contains(msg, "typed") {
		t.Fatalf("a pending line: %v %q", ok, msg)
	}
	if last := r.term.last(); last != "" {
		t.Fatalf("typed at the old session: %q", last)
	}
	// The handoff is released; the same message is now told, and only the held one has the line.
	r.must(db.AdvanceHandoff(r.DB, "h-bob", "released", "done", ""))
	if got, _ := r.TellNow(r.DB, rid); got != "sent" {
		t.Fatal(got)
	}
	if last := r.term.last(); !strings.HasPrefix(last, arrivedDuringRestart) {
		t.Fatalf("no arrival line: %q", last)
	}
	if v, _ := db.Cursor(r.DB, arrivedKey("bob", requestOp(rid))); v != 0 {
		t.Fatal("the arrival mark is kept after the message was written")
	}
	later := r.founderSays("bob", "and now?")
	if got, _ := r.TellNow(r.DB, later); got != "sent" {
		t.Fatal(got)
	}
	if last := r.term.last(); strings.Contains(last, arrivedDuringRestart) {
		t.Fatalf("a message after the restart has the line: %q", last)
	}
}

func TestAHeldBatchAndAPendingLineAreToldWithTheArrivalLine(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.decideHandoff("bob")
	r.task("bob", "alice", "a task")
	if sent, _ := r.DeliverQueued(r.DB, "bob"); sent {
		t.Fatal("the queue was told")
	}
	r.must(db.AdvanceHandoff(r.DB, "h-bob", "released", "done", ""))
	if sent, _ := r.DeliverQueued(r.DB, "bob"); !sent {
		t.Fatal("the queue was not told once the handoff ended")
	}
	if last := r.term.last(); !strings.HasPrefix(last, arrivedDuringRestart) {
		t.Fatalf("a task: %q", last)
	}
	r.decideHandoff("bob")
	r.SendMessage(r.DB, "bob", "a bare line")
	r.must(db.AdvanceHandoff(r.DB, "h-bob", "released", "done", ""))
	if sent, err := r.DeliverPending(r.DB, "bob"); err != nil || !sent {
		t.Fatalf("%v %v", sent, err)
	}
	if last := r.term.last(); last != arrivedDuringRestart+"a bare line" {
		t.Fatalf("a pending line: %q", last)
	}
}

// A handoff row nobody finished holds for NoteWaitStaleS at most.
func TestAHandoffRowLeftOpenHoldsNoLongerThanTheCeiling(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.decideHandoff("bob")
	r.clock += db.NoteWaitStaleS - 1
	if got, _ := r.TellNow(r.DB, r.ask("alice", "bob", "inside", "ask")); !strings.Contains(got, "held while it restarts") {
		t.Fatalf("not held inside the ceiling: %q", got)
	}
	r.clock += 2
	if got, _ := r.TellNow(r.DB, r.ask("alice", "bob", "past it", "ask")); got != "sent" {
		t.Fatalf("a stuck row still holds: %q", got)
	}
}

// A clear owed (the old session told it will be cleared) still holds: the row is released but the session is not yet closed.
func TestAMessageWhileAClearIsOwedGoesToTheNewSession(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.must(db.SetCursor(r.DB, "clear_owed:bob", r.now()))
	rid := r.founderSays("bob", "are you there?")
	if got, _ := r.TellNow(r.DB, rid); !strings.Contains(got, "held while it restarts") {
		t.Fatalf("told at once: %q", got)
	}
	if last := r.term.last(); last != "" {
		t.Fatalf("typed at the old session: %q", last)
	}
	r.must(db.ClearCursor(r.DB, "clear_owed:bob")) // the clear went ahead
	if got, _ := r.TellNow(r.DB, rid); got != "sent" {
		t.Fatal(got)
	}
	if last := r.term.last(); !strings.HasPrefix(last, arrivedDuringRestart) {
		t.Fatalf("no arrival line: %q", last)
	}
	// a clear owed for longer than the ceiling holds nothing
	r.must(db.SetCursor(r.DB, "clear_owed:bob", r.now()-db.NoteWaitStaleS-1))
	if got, _ := r.TellNow(r.DB, r.ask("alice", "bob", "past it", "ask")); got != "sent" {
		t.Fatalf("a stale clear holds: %q", got)
	}
}

// A message sent while a handoff trigger is deferred behind a running turn is not typed into that turn.
func TestAMessageWhileTheTriggerIsDeferredGoesToTheNewSession(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.must(db.MarkDeferred(r.DB, "bob", r.now()))
	rid := r.founderSays("bob", "are you there?")
	if got, _ := r.TellNow(r.DB, rid); !strings.Contains(got, "held while it restarts") {
		t.Fatalf("told at once: %q", got)
	}
	if last := r.term.last(); last != "" {
		t.Fatalf("typed into the running turn: %q", last)
	}
	r.must(db.ClearDeferred(r.DB, "bob"))
	if got, _ := r.TellNow(r.DB, rid); got != "sent" {
		t.Fatal(got)
	}
	if last := r.term.last(); !strings.HasPrefix(last, arrivedDuringRestart) {
		t.Fatalf("no arrival line: %q", last)
	}
}

// A batch held and then told clears the mark of each of its requests, so one of them told alone later has no line.
func TestABatchClearsTheMarksOfEachRequestItTold(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.decideHandoff("bob")
	a := r.task("bob", "alice", "first")
	b := r.task("bob", "alice", "second")
	if sent, _ := r.DeliverQueued(r.DB, "bob"); sent {
		t.Fatal("the queue was told")
	}
	r.must(db.AdvanceHandoff(r.DB, "h-bob", "released", "done", ""))
	if sent, _ := r.DeliverQueued(r.DB, "bob"); !sent {
		t.Fatal("the queue was not told")
	}
	for _, rid := range []int64{a, b} {
		if v, _ := db.Cursor(r.DB, arrivedKey("bob", requestOp(rid))); v != 0 {
			t.Fatalf("request %d keeps its arrival mark after the batch was told", rid)
		}
	}
}

// A deferral kept past the ceiling holds nothing, and the tick firing the trigger again does not start the hold over.
func TestADeferralPastTheCeilingIsNotReArmedByTheNextTick(t *testing.T) {
	r := newRig(t)
	r.must(db.MarkDeferred(r.DB, "bob", r.now()-db.NoteWaitStaleS-1))
	if held, _ := db.RestartHeld(r.DB, "bob", r.now()); held {
		t.Fatal("a deferral past the ceiling holds")
	}
	r.must(db.MarkDeferred(r.DB, "bob", r.now())) // the next tick defers the same trigger again
	if held, _ := db.RestartHeld(r.DB, "bob", r.now()); held {
		t.Fatal("the next tick re-armed the hold")
	}
	r.must(db.ClearDeferred(r.DB, "bob"))
	r.must(db.MarkDeferred(r.DB, "bob", r.now())) // a new deferral after the mark was cleared holds again
	if held, _ := db.RestartHeld(r.DB, "bob", r.now()); !held {
		t.Fatal("a new deferral does not hold")
	}
}
