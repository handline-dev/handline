package supervisor

import (
	"os"
	"path/filepath"
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

// Interrupt, Start now and the other verbs are not messages: they never ask handingOff, so only the three message paths hold.
func TestOnlyTheThreeMessagePathsAskWhetherAHandoffHolds(t *testing.T) {
	files, _ := filepath.Glob("*.go")
	var sites []string
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		b, _ := os.ReadFile(f)
		for _, line := range strings.Split(string(b), "\n") {
			if strings.Contains(line, "e.handingOff(") {
				sites = append(sites, f)
			}
		}
	}
	want := "delivery.go messaging.go sendpath.go"
	if got := strings.Join(sites, " "); got != want {
		t.Fatalf("handingOff is asked in %q, want %q", got, want)
	}
}

// A session that is started for what waits gets the held message as its first prompt, headed by the line.
func TestTheNewSessionsFirstPromptHasTheHeldMessageWithTheArrivalLine(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.decideHandoff("bob")
	r.TellNow(r.DB, r.founderSays("bob", "are you there?"))
	r.must(db.AdvanceHandoff(r.DB, "h-bob", "released", "done", ""))
	text, _, ok, err := r.WaitingPrompt(r.DB, "bob")
	if err != nil || !ok || !strings.HasPrefix(text, arrivedDuringRestart) || !strings.Contains(text, "are you there?") {
		t.Fatalf("%q %v %v", text, ok, err)
	}
}

func TestTheNewSessionsFirstPromptHasAHeldPendingLineWithTheArrivalLine(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.decideHandoff("bob")
	r.SendMessage(r.DB, "bob", "a bare line")
	r.must(db.AdvanceHandoff(r.DB, "h-bob", "released", "done", ""))
	text, _, ok, err := r.WaitingPrompt(r.DB, "bob")
	if err != nil || !ok || text != arrivedDuringRestart+"a bare line" {
		t.Fatalf("%q %v %v", text, ok, err)
	}
}
