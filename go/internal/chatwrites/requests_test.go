package chatwrites

import (
	"fmt"
	"strings"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
)

func (r *rig) state(rid int64) (state string) {
	r.t.Helper()
	if err := r.db.QueryRow("SELECT state FROM requests WHERE id = ?", rid).Scan(&state); err != nil {
		r.t.Fatal(err)
	}
	return state
}

func TestWithdrawTakesBackTheCallersOwnUndeliveredRequestAndSaysWhyNot(t *testing.T) {
	r := newRig(t)
	alice, founder := r.server("alice"), r.server("")
	withdraw := func(by int, rid int64, more, key string) map[string]any {
		t.Helper()
		s := alice
		if by == 0 {
			s = founder
		}
		return r.result(r.one(s, "request.withdraw", fmt.Sprintf(`{"id":%d%s,"request_id":%q}`, rid, more, key)))
	}
	mine := r.request("alice", "bob", "look at this")
	if res := withdraw(0, mine, "", "w1"); res["ok"] != false || !strings.Contains(res["said"].(string), "not yours to withdraw") || r.state(mine) != "queued" {
		t.Fatalf("another's request is refused in the reply: %v", res)
	}
	res := withdraw(1, mine, `,"reason":"asked the wrong one"`, "w2")
	if res["ok"] != true || res["said"] != fmt.Sprintf("request #%d to bob withdrawn", mine) || r.state(mine) != "withdrawn" {
		t.Fatalf("%v %s", res, r.state(mine))
	}
	if r.count("SELECT COUNT(*) FROM events WHERE agent = 'alice' AND kind = 'withdraw' AND payload LIKE '%asked the wrong one%'") != 1 {
		t.Fatal("the reason is kept with the withdrawal")
	}
	if res := withdraw(1, mine, "", "w3"); res["ok"] != false || !strings.Contains(res["said"].(string), "already withdrawn") {
		t.Fatalf("%v", res)
	}
	sent := r.request("alice", "bob", "too late")
	dbpkg.MarkDelivered(r.db, sent)
	if res := withdraw(1, sent, "", "w4"); res["ok"] != false || !strings.Contains(res["said"].(string), "too late to withdraw") || r.state(sent) != "queued" {
		t.Fatalf("%v", res)
	}
	if res := withdraw(1, 999999, "", "w5"); res["ok"] != false || res["said"] != "request 999999 does not exist" {
		t.Fatalf("%v", res)
	}
	own := r.request("human", "alice", "never mind")
	if res := withdraw(0, own, "", "w6"); res["ok"] != true || r.state(own) != "withdrawn" {
		t.Fatalf("with the founder's key the request is the founder's own: %v", res)
	}
	for _, bad := range []string{`{"id":"one","request_id":"w7"}`, `{"id":1,"reason":"","request_id":"w8"}`, `{"id":1,"reason":7,"request_id":"w9"}`} {
		e, _ := r.one(alice, "request.withdraw", bad)["error"].(map[string]any)
		if data, _ := e["data"].(map[string]any); data["code"] != "bad_request" {
			t.Fatalf("%s: %v", bad, e)
		}
	}
}

func TestCancelPlansWithoutWritingAndClosesAnyQueuedRequest(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	cancel := func(rid int64, more, key string) map[string]any {
		t.Helper()
		return r.result(r.one(s, "request.cancel", fmt.Sprintf(`{"id":%d%s,"request_id":%q}`, rid, more, key)))
	}
	rid := r.request("alice", "bob", "do the thing")
	dbpkg.MarkDelivered(r.db, rid)
	events := r.count("SELECT COUNT(*) FROM events")
	plan := cancel(rid, `,"dry_run":true`, "c1")
	if plan["ok"] != true || !strings.HasPrefix(plan["plan"].(string), fmt.Sprintf("cancel #%d (delivered to bob)", rid)) {
		t.Fatalf("%v", plan)
	}
	if r.state(rid) != "queued" || r.count("SELECT COUNT(*) FROM events") != events {
		t.Fatal("a dry run writes nothing")
	}
	// the same request_id is not a kept reply: the dry run and the cancel may share one
	res := cancel(rid, "", "c1")
	if res["ok"] != true || r.state(rid) != "landed" || r.count("SELECT COUNT(*) FROM requests WHERE settles = ? AND body = ?", rid, supervisor.CancelVerdict) != 1 {
		t.Fatalf("an agent's delivered request is closed with the cancel verdict: %v %s", res, r.state(rid))
	}
	if r.count("SELECT COUNT(*) FROM events WHERE kind = 'rpc_write'") != 1 {
		t.Fatal("the cancel is recorded as one write")
	}
	if res := cancel(rid, "", "c2"); res["ok"] != false || res["said"] != fmt.Sprintf("not cancelled - #%d is already landed", rid) {
		t.Fatalf("%v", res)
	}
	if plan := cancel(rid, `,"dry_run":true`, "c3"); plan["ok"] != false || plan["plan"] != fmt.Sprintf("#%d is already landed", rid) {
		t.Fatalf("%v", plan)
	}
	if res := cancel(999999, "", "c4"); res["ok"] != false || res["said"] != "not cancelled - #999999 is gone" {
		t.Fatalf("%v", res)
	}
	own := r.request("human", "alice", "never mind")
	if res := cancel(own, "", "c5"); res["ok"] != true || res["said"] != fmt.Sprintf("request #%d to alice withdrawn", own) || r.state(own) != "withdrawn" {
		t.Fatalf("the founder's own undelivered request is withdrawn: %v", res)
	}
	r.refusedWith(r.one(r.server("alice"), "request.cancel", fmt.Sprintf(`{"id":%d,"request_id":"c6"}`, r.request("alice", "bob", "x"))), "role")
	e, _ := r.one(s, "request.cancel", `{"id":1,"dry_run":"yes","request_id":"c7"}`)["error"].(map[string]any)
	if data, _ := e["data"].(map[string]any); data["field"] != "dry_run" {
		t.Fatalf("%v", e)
	}
}

func TestCancelTakesAHeldMessageBackFromTheSessionsQueue(t *testing.T) {
	r, f, s := sessionRig(t)
	f.busy = true
	rid := int64(r.result(r.one(s, "chat.send", send("take me back", "d1", "")))["request"].(float64))
	res := r.result(r.one(s, "request.cancel", fmt.Sprintf(`{"id":%d,"request_id":"c1"}`, rid)))
	if op, _ := f.ledger("bob").Get(supervisor.RequestOp(rid)); res["ok"] != true || r.state(rid) != "withdrawn" || op.State != structured.Withdrawn {
		t.Fatalf("withdrawn in the row and in the session, so the session never writes it: %v %s %v", res, r.state(rid), op)
	}
}
