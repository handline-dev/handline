package supervisor

import (
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/structured"
)

// fakeAgents is the structured sessions: what is sent is kept, and an op sent twice is answered from the first, as the real session does.
type fakeAgents struct {
	mu     sync.Mutex
	live   map[string]bool
	state  structured.State // what a new send answers
	fail   error
	sent   []sentMsg
	seen   map[string]structured.Receipt
	onSend func() // runs inside Send, before it answers: where the session's echo can come
}

type sentMsg struct {
	agent, op, text string
	d               structured.Delivery
}

func (f *fakeAgents) Structured(string) bool { return true }
func (f *fakeAgents) Live(a string) bool     { return f.live[a] }
func (f *fakeAgents) Send(a, op, text string, d structured.Delivery) (structured.Receipt, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fail != nil {
		return structured.Receipt{}, f.fail
	}
	if f.onSend != nil {
		f.mu.Unlock()
		f.onSend()
		f.mu.Lock()
	}
	if r, ok := f.seen[a+op]; ok {
		return r, nil
	}
	f.sent = append(f.sent, sentMsg{a, op, text, d})
	r := structured.Receipt{State: f.state}
	if f.seen == nil {
		f.seen = map[string]structured.Receipt{}
	}
	f.seen[a+op] = r
	return r, nil
}

func structuredRig(t *testing.T) (*rig, *fakeAgents) {
	r := newRig(t)
	fa := &fakeAgents{live: map[string]bool{"bob": true}, state: structured.Sent}
	r.Agents = fa
	return r, fa
}

func TestTellNowWritesOnceToAStructuredSession(t *testing.T) {
	r, fa := structuredRig(t)
	rid := r.ask("alice", "bob", "psst", "say")
	got, err := r.TellNow(r.DB, rid)
	r.must(err)
	if got != "sent" || len(fa.sent) != 1 || fa.sent[0].d != structured.Now || !strings.Contains(fa.sent[0].text, "psst") {
		t.Fatalf("%q %+v", got, fa.sent)
	}
	if len(r.term.typed) != 0 {
		t.Fatal("typed at a terminal")
	}
	if again, _ := r.TellNow(r.DB, rid); again == "sent" || len(fa.sent) != 1 {
		t.Fatalf("a retry wrote again: %q %+v", again, fa.sent)
	}
}

func TestSendOnceNeverWritesAnOperationTwice(t *testing.T) {
	r, fa := structuredRig(t)
	for i := 0; i < 3; i++ {
		state, _ := r.sendOnce(r.DB, "bob", "op1", "hi", structured.Now)
		if state != Written {
			t.Fatal(state)
		}
	}
	if len(fa.sent) != 1 {
		t.Fatalf("%+v", fa.sent)
	}
}

func TestSendOnceNotSentWhenNoSessionOrWriteFails(t *testing.T) {
	r, fa := structuredRig(t)
	fa.live["bob"] = false
	if state, _ := r.sendOnce(r.DB, "bob", "op1", "hi", structured.Now); state != NotSent {
		t.Fatal(state)
	}
	fa.live["bob"] = true
	fa.fail = errors.New("session closed")
	if state, why := r.sendOnce(r.DB, "bob", "op1", "hi", structured.Now); state != NotSent || why != "session closed" {
		t.Fatal(state, why)
	}
	fa.fail = nil // a failed write left no ledger row: the same op may go out now
	if state, _ := r.sendOnce(r.DB, "bob", "op1", "hi", structured.Now); state != Written {
		t.Fatal(state)
	}
}

func TestAWrittenMessageThatLosesItsSessionIsUnknownAndTold(t *testing.T) {
	r, fa := structuredRig(t)
	rid := r.ask("alice", "bob", "psst", "say")
	_, err := r.TellNow(r.DB, rid)
	r.must(err)
	r.must(r.SessionLost(r.DB, "bob"))
	if v, _ := db.Cursor(r.DB, ledgerKey("bob", requestOp(rid))); v != sendUnknown {
		t.Fatalf("ledger %d", v)
	}
	if state, _ := r.sendOnce(r.DB, "bob", requestOp(rid), "psst", structured.Now); state != Unknown || len(fa.sent) != 1 {
		t.Fatalf("an unknown message was written again: %v %+v", state, fa.sent)
	}
	var n int
	r.must(r.DB.QueryRow("SELECT count(*) FROM requests WHERE target = 'alice' AND body LIKE '%may or may not%'").Scan(&n))
	if n != 1 {
		t.Fatalf("the sender was told %d times", n)
	}
}

func TestAHeldMessageThatLosesItsSessionIsOwedAgain(t *testing.T) {
	r, fa := structuredRig(t)
	fa.state = structured.Queued
	if state, _ := r.sendOnce(r.DB, "bob", "request:7", "hi", structured.Queue); state != Held {
		t.Fatal(state)
	}
	r.must(r.SessionLost(r.DB, "bob"))
	fa.state, fa.seen = structured.Sent, nil // a new session knows nothing of the old one's operations
	if state, _ := r.sendOnce(r.DB, "bob", "request:7", "hi", structured.Queue); state != Written {
		t.Fatalf("never written, so it goes out again: %v", state)
	}
}

func TestSessionConfirmedLandsTheRequests(t *testing.T) {
	r, _ := structuredRig(t)
	rid := r.ask("alice", "bob", "do this", "ask")
	op := requestOp(rid)
	if state, _ := r.sendOnce(r.DB, "bob", op, "do this", structured.Queue); state != Written {
		t.Fatal(state)
	}
	r.must(r.SessionConfirmed(r.DB, "bob", op))
	row, _ := db.GetRequest(r.DB, rid)
	if !row.LandedAt.Valid {
		t.Fatalf("%+v", row)
	}
	if v, _ := db.Cursor(r.DB, ledgerKey("bob", op)); v != sendConfirmed {
		t.Fatal(v)
	}
}

func TestDeliverQueuedHoldsATaskBehindTheRunningTurn(t *testing.T) {
	r, fa := structuredRig(t)
	rid := r.ask("human", "bob", "ship it", "task")
	sent, err := r.DeliverQueued(r.DB, "bob")
	r.must(err)
	if !sent || len(fa.sent) != 1 || fa.sent[0].d != structured.Queue || fa.sent[0].op != requestOp(rid) {
		t.Fatalf("%v %+v", sent, fa.sent)
	}
	if sent, _ := r.DeliverQueued(r.DB, "bob"); sent && len(fa.sent) != 1 {
		t.Fatalf("a second tick wrote again: %+v", fa.sent)
	}
}

func TestNoticesAndCancelsGoThroughTheSendPath(t *testing.T) {
	r, fa := structuredRig(t)
	if ok, _ := r.TypeNotice(r.DB, "bob", "a line"); !ok {
		t.Fatal("notice not sent")
	}
	if ok, _ := r.TypeNotice(r.DB, "bob", "a line"); !ok || len(fa.sent) != 2 {
		t.Fatalf("each notice is its own operation: %+v", fa.sent)
	}
	fa.live["bob"] = false
	if ok, _ := r.TypeNotice(r.DB, "bob", "later"); ok {
		t.Fatal("sent with no session")
	}
}

func TestAnEchoDuringTheWriteIsNotOverwrittenToHeld(t *testing.T) {
	r, fa := structuredRig(t)
	fa.state = structured.Queued
	fa.onSend = func() { r.must(r.SessionConfirmed(r.DB, "bob", "request:7")) }
	state, _ := r.sendOnce(r.DB, "bob", "request:7", "hi", structured.Queue)
	if state != Confirmed {
		t.Fatalf("answered %v", state)
	}
	if v, _ := db.Cursor(r.DB, ledgerKey("bob", "request:7")); v != sendConfirmed {
		t.Fatalf("ledger %d", v)
	}
	r.must(r.SessionLost(r.DB, "bob"))
	if v, _ := db.Cursor(r.DB, ledgerKey("bob", "request:7")); v != sendConfirmed {
		t.Fatalf("a lost session unsettled a confirmed message: %d", v)
	}
}

func TestHeldAnswersAreAnnouncedOnlyOnceTheyAreOut(t *testing.T) {
	r, fa := structuredRig(t)
	fa.live["bob"] = false
	r.answered("bob") // while it has no session, so nothing tells it yet
	fa.live["bob"] = true
	fa.state = structured.Queued
	if sent, _ := r.DeliverAnswers(r.DB, "bob"); sent {
		t.Fatal("held answers were announced")
	}
	if got, _ := db.UnannouncedAnswers(r.DB, "bob"); len(got) != 1 {
		t.Fatalf("%d unannounced while held", len(got))
	}
	r.must(r.SessionLost(r.DB, "bob")) // the session ended before the turn did: owed again
	fa.state, fa.seen = structured.Sent, nil
	if sent, _ := r.DeliverAnswers(r.DB, "bob"); !sent || len(fa.sent) != 2 {
		t.Fatalf("not told again: %v %+v", sent, fa.sent)
	}
	if got, _ := db.UnannouncedAnswers(r.DB, "bob"); len(got) != 0 {
		t.Fatal("still unannounced after the write")
	}
}

func TestHeldAnswersAreAnnouncedWhenTheEchoComes(t *testing.T) {
	r, fa := structuredRig(t)
	fa.live["bob"] = false
	r.answered("bob") // while it has no session, so nothing tells it yet
	fa.live["bob"] = true
	fa.state = structured.Queued
	r.DeliverAnswers(r.DB, "bob")
	var op string
	r.must(r.DB.QueryRow("SELECT substr(name, length('send:bob:')+1) FROM cursors WHERE name LIKE 'send:bob:answers:%'").Scan(&op))
	r.must(r.SessionConfirmed(r.DB, "bob", op))
	if got, _ := db.UnannouncedAnswers(r.DB, "bob"); len(got) != 0 {
		t.Fatal("not announced on the echo")
	}
}

func TestOneStuckSessionDoesNotBlockAnotherAgentsSend(t *testing.T) {
	r, fa := structuredRig(t)
	fa.live["alice"] = true
	release, entered := make(chan struct{}), make(chan struct{})
	fa.onSend = func() {
		select {
		case entered <- struct{}{}:
			<-release
		default:
		}
	}
	stuck := make(chan struct{})
	go func() { r.sendOnce(r.DB, "bob", "op1", "stuck", structured.Now); close(stuck) }()
	<-entered
	done := make(chan struct{})
	go func() { r.sendOnce(r.DB, "alice", "op2", "free", structured.Now); close(done) }()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("alice waited behind bob's write")
	}
	close(release)
	<-stuck
}

func TestAnUnknownPendingOrAnswersOpIsRecordedAndNotWrittenAgain(t *testing.T) {
	r, fa := structuredRig(t)
	for _, op := range []string{"pending:3", "answers:4"} {
		r.sendOnce(r.DB, "bob", op, "x", structured.Now)
	}
	r.must(r.SessionLost(r.DB, "bob"))
	for _, op := range []string{"pending:3", "answers:4"} {
		if state, _ := r.sendOnce(r.DB, "bob", op, "x", structured.Now); state != Unknown {
			t.Fatalf("%s: %v", op, state)
		}
	}
	if len(fa.sent) != 2 {
		t.Fatalf("%+v", fa.sent)
	}
}

// answered gives bob an answer that came back from alice, not yet announced.
func (r *rig) answered(asker string) {
	r.t.Helper()
	rid := r.ask(asker, "alice", "q?", "ask")
	_, _, err := r.Resolve(r.DB, ResolveIn{RID: rid, Verdict: "the answer", Notify: true})
	r.must(err)
	if got, _ := db.UnannouncedAnswers(r.DB, asker); len(got) == 0 {
		r.t.Fatal("no answer to announce")
	}
}

func TestAnswersThatArriveWhileOthersAreHeldAreNotToldTwice(t *testing.T) {
	r, fa := structuredRig(t)
	fa.live["bob"] = false
	r.answered("bob")
	fa.live["bob"], fa.state = true, structured.Queued
	r.DeliverAnswers(r.DB, "bob") // answer one is held behind the running turn
	fa.live["bob"] = false
	r.answered("bob") // a second one arrives during the same turn
	fa.live["bob"] = true
	r.DeliverAnswers(r.DB, "bob")
	r.DeliverAnswers(r.DB, "bob")
	var told int
	for _, m := range fa.sent {
		if strings.Contains(m.text, "the answer") {
			told += strings.Count(m.text, "the answer")
		}
	}
	if told != 2 {
		t.Fatalf("the answers were written %d times in all, want once each: %+v", told, fa.sent)
	}
}

func TestAWrittenAnswersBatchIsAnnouncedOnceNotAgainByItsEcho(t *testing.T) {
	r, fa := structuredRig(t)
	fa.live["bob"] = false
	r.answered("bob")
	fa.live["bob"] = true
	if sent, _ := r.DeliverAnswers(r.DB, "bob"); !sent {
		t.Fatal("not sent")
	}
	var op string
	r.must(r.DB.QueryRow("SELECT substr(name, length('send:bob:')+1) FROM cursors WHERE name LIKE 'send:bob:answers:%'").Scan(&op))
	r.must(r.SessionConfirmed(r.DB, "bob", op))
	var n int
	r.DB.QueryRow("SELECT COUNT(*) FROM events WHERE agent = 'bob' AND kind = 'answered'").Scan(&n)
	if n != 1 {
		t.Fatalf("%d answered events", n)
	}
}
