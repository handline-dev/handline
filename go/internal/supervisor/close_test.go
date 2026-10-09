package supervisor

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func TestWithdrawTakesBackAnUndeliveredRequestOnlyForItsRequester(t *testing.T) {
	r := newRig(t)
	rid := r.ask("alice", "bob", "oops", "ask")
	after := &After{}
	if ok, msg, _ := r.WithdrawWrite(r.DB, after, rid, "bob", "", nil); ok || !strings.Contains(msg, "not yours to withdraw") {
		t.Fatalf("%v %s", ok, msg)
	}
	ok, msg, err := r.WithdrawWrite(r.DB, after, rid, "alice", "wrong target", nil)
	r.must(err)
	if !ok || msg != "request #1 to bob withdrawn" {
		t.Fatalf("%v %s", ok, msg)
	}
	row, _ := db.GetRequest(r.DB, rid)
	if row.State != "withdrawn" || row.Answer.String != "withdrawn: wrong target" {
		t.Fatalf("%+v", row)
	}
	if ok, msg, _ := r.WithdrawWrite(r.DB, after, rid, "alice", "", nil); ok || !strings.Contains(msg, "already withdrawn") {
		t.Fatalf("%s", msg)
	}
	// once delivered it is too late
	rid2 := r.ask("alice", "bob", "second", "ask")
	db.MarkDelivered(r.DB, rid2)
	ok, msg, _ = r.WithdrawWrite(r.DB, after, rid2, "alice", "", nil)
	if ok || !strings.Contains(msg, "was already delivered to bob - too late to withdraw; tell bob with a message (`hl say bob \"...\" --parent 2`)") {
		t.Fatalf("%s", msg)
	}
}

func TestCancelClosesADeliveredRequestAndTellsItsAgentToStop(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.agentUp("alice")
	rid := r.ask("alice", "bob", "big job", "ask")
	if got, _ := r.TellNow(r.DB, rid); got != "typed" {
		t.Fatal(got)
	}
	after := &After{}
	plan, ok, msg, err := r.CancelWrite(r.DB, after, rid, nil, nil)
	r.must(err)
	if !ok || plan == nil {
		t.Fatal(msg)
	}
	out, err := r.CancelTell(r.DB, plan)
	r.must(err)
	if !strings.HasPrefix(out, "#1 to bob cancelled (bob was told to stop)\nrequest #1 -> rejected") {
		t.Fatalf("%q", out)
	}
	if got := r.term.typed[len(r.term.typed)-2]; got != "#1 from alice was cancelled: withdrawn by the founder. Stop work on it; it is closed and needs no resolve. `hl read 1` shows it." {
		t.Fatalf("%q", got)
	}
	row, _ := db.GetRequest(r.DB, rid)
	if row.State != "landed" || row.Answer.String != "withdrawn by the founder" {
		t.Fatalf("%+v", row)
	}
	if yes, _ := db.ClosedUnder(r.DB, rid); !yes {
		t.Fatal("no closed_under event")
	}
	if _, ok, msg, _ := r.CancelWrite(r.DB, after, rid, nil, nil); ok || !strings.Contains(msg, "already landed - nothing to cancel") {
		t.Fatalf("%s", msg)
	}
}

func TestCancelOfTheFoundersUndeliveredRequestIsAWithdrawal(t *testing.T) {
	r := newRig(t)
	rid := r.ask("human", "bob", "do x", "")
	after := &After{}
	plan, ok, msg, err := r.CancelWrite(r.DB, after, rid, nil, nil)
	r.must(err)
	if !ok || plan != nil || msg != "request #1 to bob withdrawn" {
		t.Fatalf("%v %v %q", ok, plan, msg)
	}
}

func TestCancelGuardRefusesInsideTheTransaction(t *testing.T) {
	r := newRig(t)
	rid := r.ask("alice", "bob", "job", "ask")
	db.MarkDelivered(r.DB, rid)
	_, ok, msg, _ := r.CancelWrite(r.DB, &After{}, rid, nil, func(db.Q, *db.Request) string { return "it landed meanwhile" })
	if ok || msg != "it landed meanwhile" {
		t.Fatalf("%v %q", ok, msg)
	}
	if row, _ := db.GetRequest(r.DB, rid); row.State != "queued" {
		t.Fatal("closed despite the guard")
	}
}

func TestResolveRefusesToOverwriteAnAnswerAndQueuesTheAskerOnce(t *testing.T) {
	r := newRig(t)
	rid := r.ask("alice", "bob", "q", "ask")
	ok, msg, err := r.Resolve(r.DB, ResolveIn{RID: rid, Verdict: "yes", Notify: true})
	r.must(err)
	if !ok || !strings.HasPrefix(msg, "request #1 -> done") {
		t.Fatalf("%v %q", ok, msg)
	}
	if len(r.eval.calls) != 1 || r.eval.calls[0] != "alice: request 1 resolved: done" {
		t.Fatalf("%v", r.eval.calls)
	}
	if ok, msg, _ := r.Resolve(r.DB, ResolveIn{RID: rid, Verdict: "no", Notify: true}); ok || msg != "#1 is already landed - nothing to resolve" {
		t.Fatalf("%v %q", ok, msg)
	}
	if ok, msg, _ := r.Resolve(r.DB, ResolveIn{RID: 99}); ok || msg != "request 99 does not exist" {
		t.Fatal(msg)
	}
	// not while the asker still has another async ask out
	a := r.ask("alice", "bob", "q1", "ask")
	r.ask("alice", "bob", "q2", "ask")
	r.eval.calls = nil
	r.Resolve(r.DB, ResolveIn{RID: a, Verdict: "ok", Notify: true})
	if len(r.eval.calls) != 0 {
		t.Fatalf("the asker is queued with another ask outstanding: %v", r.eval.calls)
	}
}

func TestResolveOpensTasksInTheSameWriteAndNamesThem(t *testing.T) {
	r := newRig(t)
	rid := r.ask("alice", "bob", "plan?", "ask")
	tx, err := r.DB.Begin()
	r.must(err)
	res, ok, msg, err := r.ResolveWrite(tx, ResolveIn{RID: rid, Verdict: "do it", Notify: true,
		OpenTasks: func(q db.Q) ([]int64, error) {
			a, _ := db.NewTask(q, "step", "bob", "bob", 0, db.DefaultPriority, "", 0)
			return []int64{a}, nil
		}})
	r.must(err)
	if !ok {
		t.Fatal(msg)
	}
	r.must(tx.Commit())
	if res.Verdict != "do it\n\nOpened as task(s): T1" {
		t.Fatalf("%q", res.Verdict)
	}
	if row, _ := db.GetRequest(r.DB, rid); row.Answer.String != res.Verdict {
		t.Fatalf("%q", row.Answer.String)
	}
}

func TestResolveRefusedLeavesNothingWhenItLostTheRace(t *testing.T) {
	r := newRig(t)
	rid := r.ask("alice", "bob", "q", "ask")
	db.CloseIfQueued(r.DB, rid, "done", "first", db.CloseOpts{})
	tx, _ := r.DB.Begin()
	defer tx.Rollback()
	_, ok, msg, _ := r.ResolveWrite(tx, ResolveIn{RID: rid, Verdict: "second"})
	if ok || msg != "#1 is already landed - nothing to resolve" {
		t.Fatalf("%v %q", ok, msg)
	}
	if a, _ := db.AnswerRowOf(tx, rid); a == nil || a.Body != "first" {
		t.Fatalf("%+v", a)
	}
}

func TestRetireWriteClosesAHeldRequestWithoutSettlingItsDecisionTask(t *testing.T) {
	r := newRig(t)
	r.agentUp("alice")
	card := r.ask("alice", "human", "decide", "")
	tid, _ := db.DecisionTaskOfCard(r.DB, card)
	if tid == 0 {
		t.Fatal("no decision task")
	}
	no := false
	plan, ok, why, err := r.RetireWrite(r.DB, card, "released", &no, false)
	r.must(err)
	if !ok {
		t.Fatal(why)
	}
	if task, _ := db.GetTask(r.DB, tid); task.Ended() {
		t.Fatal("a release settled the task")
	}
	if _, err := r.RetireTell(r.DB, plan); err != nil {
		t.Fatal(err)
	}
	if _, ok, why, _ := r.RetireWrite(r.DB, card, "again", nil, true); ok || why != "#1 is already withdrawn - nothing to retire" {
		t.Fatalf("%v %q", ok, why)
	}
}

// A name no agent directory backs has no state to queue: the write made `hl ps` list it as an agent.
func TestResolveQueuesNoStateRowForARequesterThatIsNoAgent(t *testing.T) {
	r := newRig(t)
	rid := r.ask("ghost", "bob", "q", "ask")
	_, _, err := r.Resolve(r.DB, ResolveIn{RID: rid, Verdict: "yes", Notify: true})
	r.must(err)
	if len(r.eval.calls) != 0 {
		t.Fatalf("a requester no agent has was queued: %v", r.eval.calls)
	}
}
