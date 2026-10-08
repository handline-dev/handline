package chatwrites

import (
	"fmt"
	"strings"
	"sync"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
)

// fakeSessions is the structured sessions as chat.send sees them: bob has one, alice is a terminal agent. A session records what it takes in
// the operations table, as the real one does; busy makes a queue message wait, and echo confirms what is written.
type fakeSessions struct {
	mu      sync.Mutex
	r       *rig
	busy    bool
	busyFor map[string]bool // agents with a turn running, besides the one busy covers
	echo    bool
	sent    []string
}

func (f *fakeSessions) Structured(a string) bool { return a != "alice" }
func (f *fakeSessions) Live(a string) bool       { return a != "alice" }

func (f *fakeSessions) ledger(a string) *structured.DBLedger {
	return &structured.DBLedger{DB: f.r.db, Agent: a}
}

func (f *fakeSessions) Send(a, op, text string, d structured.Delivery) (structured.Receipt, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	l := f.ledger(a)
	if o, _ := l.Get(op); o != nil {
		return structured.Receipt{Op: o.ID, UUID: o.UUID, State: o.State}, nil
	}
	o := structured.Op{ID: op, UUID: structured.UUIDFor(op), Text: text, Delivery: d, State: structured.Sent}
	switch {
	case d == structured.Queue && (f.busy || f.busyFor[a]):
		o.State = structured.Queued
	case f.echo:
		o.State = structured.Confirmed
		f.sent = append(f.sent, text)
	default:
		f.sent = append(f.sent, text)
	}
	l.Put(o)
	return structured.Receipt{Op: op, UUID: o.UUID, State: o.State}, nil
}

func (f *fakeSessions) Withdraw(a, op string) (structured.Receipt, bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	l := f.ledger(a)
	o, _ := l.Get(op)
	if o == nil {
		return structured.Receipt{}, false, fmt.Errorf("no such operation")
	}
	if o.State == structured.Queued {
		o.State = structured.Withdrawn
		l.Put(*o)
	}
	return structured.Receipt{Op: op, UUID: o.UUID, State: o.State}, o.State == structured.Withdrawn, nil
}

func (f *fakeSessions) Promote(a, op string) (structured.Receipt, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	l := f.ledger(a)
	o, _ := l.Get(op)
	if o == nil {
		return structured.Receipt{}, fmt.Errorf("no such operation")
	}
	if o.State == structured.Queued {
		o.State = structured.Sent
		l.Put(*o)
		f.sent = append(f.sent, o.Text)
	}
	return structured.Receipt{Op: op, UUID: o.UUID, State: o.State}, nil
}

// confirmAll is the CLI echoing every message written so far.
func (f *fakeSessions) confirmAll() {
	rows, _ := f.r.db.Query("SELECT id FROM operations WHERE kind = 'send' AND state = 'sent'")
	var ids []string
	for rows.Next() {
		var id string
		rows.Scan(&id)
		ids = append(ids, id)
	}
	rows.Close()
	for _, id := range ids {
		parts := strings.SplitN(id, ":", 3) // send:<agent>:<op>
		f.setState(parts[1], parts[2], structured.Confirmed)
		f.r.env.SessionConfirmed(f.r.db, parts[1], parts[2])
	}
}

// setState is the session moving a message on: the CLI echoed it, or the daemon died before it did.
func (f *fakeSessions) setState(a, op string, s structured.State) {
	l := f.ledger(a)
	o, _ := l.Get(op)
	o.State = s
	l.Put(*o)
}

func sessionRig(t *testing.T) (*rig, *fakeSessions, *rpc.Server) {
	r := newRig(t)
	f := &fakeSessions{r: r}
	r.env.Agents = f
	return r, f, r.server("")
}

func send(body, rid, extra string) string {
	return fmt.Sprintf(`{"agent":"bob","body":%q,"request_id":%q%s}`, body, rid, extra)
}

// receiptOf is the receipt of a result, whose uuid is the one derived from the request's operation.
func receiptOf(t *testing.T, res map[string]any) map[string]any {
	t.Helper()
	rc, ok := res["receipt"].(map[string]any)
	if !ok {
		t.Fatalf("no receipt in %v", res)
	}
	if rc["uuid"] != structured.UUIDFor(supervisor.RequestOp(int64(res["request"].(float64)))) {
		t.Fatalf("receipt uuid %v is not the operation's", rc["uuid"])
	}
	return rc
}

func (r *rig) delivered(rid int64) (delivered, typed bool) {
	r.t.Helper()
	return r.count("SELECT COUNT(*) FROM requests WHERE id = ? AND delivered_at IS NOT NULL", rid) == 1,
		r.count("SELECT COUNT(*) FROM requests WHERE id = ? AND typed_at IS NOT NULL", rid) == 1
}

func TestAMessageToAnIdleStructuredAgentIsWrittenAtOnceAndAnswersItsReceipt(t *testing.T) {
	r, f, s := sessionRig(t)
	res := r.result(r.one(s, "chat.send", send("idle hello", "d1", "")))
	if rc := receiptOf(t, res); rc["state"] != "sent" {
		t.Fatalf("written: %v", rc)
	}
	rid := int64(res["request"].(float64))
	if d, ty := r.delivered(rid); !d || !ty || len(f.sent) != 1 {
		t.Fatalf("delivered and typed once: %v %v %v", d, ty, f.sent)
	}
	f.echo = true
	if rc := receiptOf(t, r.result(r.one(s, "chat.send", send("again", "d2", `,"delivery":"now"`)))); rc["state"] != "confirmed" {
		t.Fatalf("an echo that came with the write: %v", rc)
	}
}

func TestAWorkingAgentQueuesByDefaultAndNowJoinsTheTurn(t *testing.T) {
	r, f, s := sessionRig(t)
	f.busy = true
	res := r.result(r.one(s, "chat.send", send("later", "d1", "")))
	if rc := receiptOf(t, res); rc["state"] != "queued" {
		t.Fatalf("held: %v", rc)
	}
	rid := int64(res["request"].(float64))
	if d, ty := r.delivered(rid); d || ty || len(f.sent) != 0 {
		t.Fatalf("a held message is not delivered: %v %v %v", d, ty, f.sent)
	}
	if rc := receiptOf(t, r.result(r.one(s, "chat.send", send("by word", "d2", `,"delivery":"queue"`)))); rc["state"] != "queued" {
		t.Fatal(rc)
	}
	if rc := receiptOf(t, r.result(r.one(s, "chat.send", send("now", "d3", `,"delivery":"now"`)))); rc["state"] != "sent" {
		t.Fatalf("now is written at once: %v", rc)
	}
	e := r.one(s, "chat.send", send("x", "d4", `,"delivery":"later"`))["error"].(map[string]any)
	if data := e["data"].(map[string]any); data["code"] != "bad_request" || data["field"] != "delivery" {
		t.Fatalf("another word: %v", e)
	}
}

func TestARepeatAnswersTheReceiptAsItIsNowAndOtherParamsAreRefused(t *testing.T) {
	r, f, s := sessionRig(t)
	first := r.result(r.one(s, "chat.send", send("repeat", "d1", "")))
	if receiptOf(t, first)["state"] != "sent" {
		t.Fatal(first)
	}
	f.setState("bob", supervisor.RequestOp(int64(first["request"].(float64))), structured.Confirmed)
	again := r.result(r.one(s, "chat.send", send("repeat", "d1", "")))
	if again["replayed"] != true || again["request"] != first["request"] || receiptOf(t, again)["state"] != "confirmed" || len(f.sent) != 1 {
		t.Fatalf("the receipt now, nothing written again: %v %v", again, f.sent)
	}
	e := r.one(s, "chat.send", send("other", "d1", ""))["error"].(map[string]any)
	if data := e["data"].(map[string]any); data["code"] != "bad_request" || data["field"] != "request_id" {
		t.Fatalf("same id, other params: %v", e)
	}
}

func TestAnUnknownOperationStaysUnknownAndANewIdIsANewOperation(t *testing.T) {
	r, f, s := sessionRig(t)
	first := r.result(r.one(s, "chat.send", send("in flight", "crash-1", "")))
	rid := int64(first["request"].(float64))
	f.setState("bob", supervisor.RequestOp(rid), structured.Unknown)
	again := r.result(r.one(s, "chat.send", send("in flight", "crash-1", "")))
	if again["replayed"] != true || receiptOf(t, again)["state"] != "unknown" || len(f.sent) != 1 {
		t.Fatalf("unknown, and not written again: %v", again)
	}
	fresh := r.result(r.one(s, "chat.send", send("in flight", "crash-2", "")))
	if fresh["request"] == first["request"] || receiptOf(t, fresh)["state"] != "sent" || len(f.sent) != 2 {
		t.Fatalf("a new operation: %v", fresh)
	}
}

func TestCancelWithdrawsAQueuedMessageWithoutConfirmAndARetryOfItIsRefused(t *testing.T) {
	r, f, s := sessionRig(t)
	f.busy = true
	res := r.result(r.one(s, "chat.send", send("take me back", "d1", "")))
	rid := int64(res["request"].(float64))
	r.result(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"request_id":"c1"}`, rid)))
	var state string
	r.db.QueryRow("SELECT state FROM requests WHERE id = ?", rid).Scan(&state)
	if op, _ := f.ledger("bob").Get(supervisor.RequestOp(rid)); state != "withdrawn" || op.State != structured.Withdrawn {
		t.Fatalf("withdrawn in the row and in the session: %s %v", state, op)
	}
	r.refusedWith(r.one(s, "chat.send", send("take me back", "d1", "")), "state")
}

func TestCancelOfAWrittenMessageNeedsTheConfirm(t *testing.T) {
	r, f, s := sessionRig(t)
	res := r.result(r.one(s, "chat.send", send("too late", "d1", "")))
	rid := int64(res["request"].(float64))
	r.refusedWith(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"request_id":"c1"}`, rid)), "confirm")
	// a message the session wrote while the person was deciding is written too: the cancel asks for the confirm
	f.busy = true
	held := int64(r.result(r.one(s, "chat.send", send("race", "d2", "")))["request"].(float64))
	f.setState("bob", supervisor.RequestOp(held), structured.Sent)
	r.refusedWith(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"request_id":"c2"}`, held)), "confirm")
	if d, ty := r.delivered(held); !d || !ty {
		t.Fatalf("marked as written: %v %v", d, ty)
	}
}

func TestCancelOfAConfirmedMessageNeedsTheConfirmAndTellsTheAgentToStop(t *testing.T) {
	r, f, s := sessionRig(t)
	rid := int64(r.result(r.one(s, "chat.send", send("stop me", "d1", "")))["request"].(float64))
	f.confirmAll()
	r.refusedWith(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"request_id":"c1"}`, rid)), "confirm")
	var state string
	r.db.QueryRow("SELECT state FROM requests WHERE id = ?", rid).Scan(&state)
	if state != "landed" {
		t.Fatalf("a refused cancel leaves the message as it was: %s", state)
	}
	r.result(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"confirm":true,"request_id":"c2"}`, rid)))
	if r.count("SELECT COUNT(*) FROM requests WHERE settles = ? AND body = ?", rid, supervisor.CancelVerdict) != 1 {
		t.Fatal("closed as rejected with the cancel verdict")
	}
	row, _ := dbpkg.GetRequest(r.db, rid)
	if typed, why, err := r.env.NotifyCancelled(r.db, row, supervisor.CancelVerdict); !typed || err != nil || len(f.sent) != 2 || !strings.Contains(f.sent[1], "cancel") {
		t.Fatalf("the agent is told to stop (the telling itself waits for a wired daemon): %v %q %v %q", typed, why, err, f.sent)
	}
	r.refusedWith(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"confirm":true,"request_id":"c3"}`, rid)), "state")
}

func TestSendNowWritesAQueuedMessageAndRefusesAnyOther(t *testing.T) {
	r, f, s := sessionRig(t)
	f.busy = true
	rid := int64(r.result(r.one(s, "chat.send", send("go now", "d1", "")))["request"].(float64))
	res := r.result(r.one(s, "chat.send_now", fmt.Sprintf(`{"request":%d,"request_id":"n1"}`, rid)))
	if res["receipt"].(map[string]any)["state"] != "sent" || len(f.sent) != 1 {
		t.Fatalf("written now: %v %v", res, f.sent)
	}
	if d, ty := r.delivered(rid); !d || !ty {
		t.Fatalf("delivered and typed: %v %v", d, ty)
	}
	r.refusedWith(r.one(s, "chat.send_now", fmt.Sprintf(`{"request":%d,"request_id":"n2"}`, rid)), "state")
}

func TestATerminalAgentAcceptsDeliveryAndHasNoReceipt(t *testing.T) {
	r, _, s := sessionRig(t)
	res := r.result(r.one(s, "chat.send", `{"agent":"alice","body":"to a terminal","request_id":"d1","delivery":"now"}`))
	if _, has := res["receipt"]; has || res["accepted"] != true {
		t.Fatalf("no receipt: %v", res)
	}
	if again := r.result(r.one(s, "chat.send", `{"agent":"alice","body":"to a terminal","request_id":"d1","delivery":"now"}`)); again["receipt"] != nil {
		t.Fatalf("nor on a repeat: %v", again)
	}
}
