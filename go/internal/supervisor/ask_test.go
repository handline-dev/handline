package supervisor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func TestAskQueuesWritesTheInboxFileAndTellsOnlyAfterTheCommit(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	out, err := r.Ask(r.DB, AskIn{Target: "bob", Body: "please look", Requester: "alice", Notify: false})
	r.must(err)
	if !out.OK || out.RID == 0 || !strings.HasPrefix(out.Message, "queued as #") || !strings.Contains(out.Message, "for bob (response=async, 0/5 asks this hour)") {
		t.Fatalf("%+v", out)
	}
	if _, err := os.Stat(filepath.Join(r.home, "agents", "bob", "inbox", inboxName(out.RID))); err == nil {
		t.Fatal("the inbox file was written before the commit")
	}
	out.Finish()
	b, err := os.ReadFile(filepath.Join(r.home, "agents", "bob", "inbox", inboxName(out.RID)))
	r.must(err)
	want := `{
  "id": 1,
  "ts": 100000,
  "from": "alice",
  "to": "bob",
  "relation": "peer",
  "kind": null,
  "response": "async",
  "chain_depth": 0,
  "files": [],
  "body": "please look"
}
`
	if string(b) != want {
		t.Fatalf("%s", b)
	}
	row, _ := db.GetRequest(r.DB, out.RID)
	if row.State != "queued" || row.EdgeOK.Int64 != 1 || row.ChainID.String != "1" {
		t.Fatalf("%+v", row)
	}
	if n, _, _ := db.LastEventID(r.DB, "alice", "ask"); n == 0 {
		t.Fatal("no ask event")
	}
}

func TestAskRefusesAnImpersonatedFounderAndAnUnknownAsker(t *testing.T) {
	r := newRig(t)
	agent := "alice"
	out, _ := r.Ask(r.DB, AskIn{Target: "bob", Body: "x", Requester: "human", Speaker: &agent})
	if out.OK || !strings.Contains(out.Message, "alice may not ask as human") {
		t.Fatalf("%+v", out)
	}
	if out, _ := r.Ask(r.DB, AskIn{Target: "bob", Body: "x", Requester: "human", Speaker: &agent, OnBehalfOf: true}); !out.OK {
		t.Fatalf("a system delivery on behalf of the founder: %+v", out)
	}
	if out, _ := r.Ask(r.DB, AskIn{Target: "bob", Body: "x"}); out.OK || !strings.Contains(out.Message, "cannot tell who is asking") {
		t.Fatalf("%+v", out)
	}
}

func TestADeniedAskKeepsItsTextOnceAndSaysWhy(t *testing.T) {
	r := newRig(t)
	for i := 0; i < 2; i++ {
		out, err := r.Ask(r.DB, AskIn{Target: "carol", Body: "hi", Requester: "alice"})
		r.must(err)
		if out.OK || out.Message != "unknown target 'carol'" {
			t.Fatalf("%+v", out)
		}
	}
	var n int
	r.DB.QueryRow("SELECT COUNT(*) FROM requests WHERE state = 'denied'").Scan(&n)
	if n != 1 {
		t.Fatalf("the same refusal is a second row: %d", n)
	}
}

func TestTheRateLimitThrottlesTheAskerAndKeepsTheText(t *testing.T) {
	r := newRig(t)
	for i := 0; i < 5; i++ {
		out, _ := r.Ask(r.DB, AskIn{Target: "bob", Body: "n" + string(rune('a'+i)), Requester: "alice"})
		if !out.OK {
			t.Fatalf("ask %d: %s", i, out.Message)
		}
	}
	out, err := r.Ask(r.DB, AskIn{Target: "bob", Body: "one too many", Requester: "alice"})
	r.must(err)
	if out.OK || !strings.Contains(out.Message, "rate limit: 5 asks in the last hour >= 5 - alice is THROTTLED") || !strings.Contains(out.Message, "Your text is kept as #") {
		t.Fatalf("%s", out.Message)
	}
	s, _ := db.GetAgentState(r.DB, "alice")
	if s.State != "THROTTLED" || s.WakeAt.Int64 != 100000+3600 {
		t.Fatalf("%+v", s)
	}
	again, _ := r.Ask(r.DB, AskIn{Target: "bob", Body: "one too many", Requester: "alice"})
	if again.OK {
		t.Fatal("asked while throttled")
	}
	var n int
	r.DB.QueryRow("SELECT COUNT(*) FROM requests WHERE body = 'one too many'").Scan(&n)
	if n != 1 {
		t.Fatalf("an identical retry wrote %d rows", n)
	}
}

func TestACycleInAChainIsRaisedToTheFounderButASayBackIsNot(t *testing.T) {
	r := newRig(t)
	first, _ := r.Ask(r.DB, AskIn{Target: "bob", Body: "start", Requester: "alice"})
	out, err := r.Ask(r.DB, AskIn{Target: "alice", Body: "back to you", Requester: "bob", ParentID: first.RID, Kind: "ask"})
	r.must(err)
	if out.OK || !strings.Contains(out.Message, fmt.Sprintf("cycle: alice already asked bob in this chain (request #%d), and this would send it straight back", first.RID)) ||
		!strings.Contains(out.Message, "Raised to the founder as #") {
		t.Fatalf("%+v", out)
	}
	out.Finish()
	if ids, _ := db.OpenDecisionCardIDs(r.DB, "bob", "[stopped] bob -> alice was stopped: cycle", nil); len(ids) != 1 {
		t.Fatalf("the runaway is not on the founder's board: %v", ids)
	}
	if reply, _, err := r.Say(r.DB, SayIn{Target: "alice", Body: "back to you", Requester: "bob", ParentID: first.RID}); err != nil || !reply.OK {
		t.Fatalf("a say back is the ordinary shape: %+v %v", reply, err)
	}
}

func TestTheDecisionRaisedForARunawayIsOneCardPerShape(t *testing.T) {
	r := newRig(t)
	after := &After{}
	a := r.raiseDecision(r.DB, after, "alice", "need a call\nmore", "why", "", "", 0, 0)
	b := r.raiseDecision(r.DB, after, "alice", "need a call\nmore", "why", "", "", 0, 0)
	if a != b {
		t.Fatalf("the same text still waiting is a second card: %d %d", a, b)
	}
	c := r.raiseDecision(r.DB, after, "alice", "a different call", "why", "", "", 0, 0)
	if c == a {
		t.Fatal("a different text shares a card")
	}
	card, _ := db.GetRequest(r.DB, a)
	if card.Target != "human" || card.Kind.String != "task" || card.Response.String != "fire_and_forget" {
		t.Fatalf("%+v", card)
	}
	tid, _ := db.DecisionTaskOfCard(r.DB, a)
	task, _ := db.GetTask(r.DB, tid)
	if task.Owner != "human" || task.OpenedBy != "alice" || task.Title != "need a call" || task.Expects.String != "decision" || task.QueuedBy.String != "human" {
		t.Fatalf("%+v", task)
	}
	after.Run()
	if _, err := os.Stat(filepath.Join(r.home, "agents", "human", "inbox", inboxName(a))); err != nil {
		t.Fatal(err)
	}
}

func TestReportToTheFounderIsBornLandedAndAsksNothing(t *testing.T) {
	r := newRig(t)
	after := &After{}
	rid := r.report(r.DB, after, "alice", "all quiet", "fyi")
	row, _ := db.GetRequest(r.DB, rid)
	if row.State != "landed" || row.Kind.String != "say" || row.ReadAt.Valid {
		t.Fatalf("%+v", row)
	}
}

func TestAnAskToTheFounderOpensADecisionTask(t *testing.T) {
	r := newRig(t)
	out, err := r.Ask(r.DB, AskIn{Target: "human", Body: "which db?\nlong text", Requester: "alice"})
	r.must(err)
	if !out.OK {
		t.Fatal(out.Message)
	}
	tid, _ := db.DecisionTaskOfCard(r.DB, out.RID)
	if tid == 0 {
		t.Fatal("no decision task")
	}
	// task new says NoCard and opens its own
	out2, _ := r.Ask(r.DB, AskIn{Target: "human", Body: "other", Requester: "alice", NoCard: true})
	if tid, _ := db.DecisionTaskOfCard(r.DB, out2.RID); tid != 0 {
		t.Fatal("NoCard still opened a task")
	}
}

func TestSayRepliesSettleTheQuestionInTheSameWrite(t *testing.T) {
	r := newRig(t)
	q, _ := r.Ask(r.DB, AskIn{Target: "bob", Body: "what time?", Requester: "alice"})
	out, settled, err := r.Say(r.DB, SayIn{Target: "alice", Body: "noon", Requester: "bob", ParentID: q.RID})
	r.must(err)
	if !out.OK || settled == nil {
		t.Fatalf("%+v %+v", out, settled)
	}
	question, _ := db.GetRequest(r.DB, q.RID)
	if question.State != "landed" || question.Answer.String != "noon" {
		t.Fatalf("%+v", question)
	}
	ans, _ := db.AnswerRowOf(r.DB, q.RID)
	if ans == nil || ans.ID != out.RID || ans.Settles.Int64 != q.RID || ans.State != "queued" {
		t.Fatalf("the reply is the answer, still to be told: %+v", ans)
	}
}
