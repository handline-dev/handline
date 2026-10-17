package taskwrites

import (
	"fmt"
	"testing"
)

func TestEditChangesATaskInOneTransactionAndLeavesTheTellingToTheTeller(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, priority) VALUES(1, 'old title', 'worker', 'manager', 1, 2), (2, 'wait', 'other', 'manager', 1, 2)",
		"INSERT INTO requests(id, requester, target, body, state, kind, queue_order, ts) VALUES(10, 'manager', 'worker', 'the brief', 'queued', 'task', 10, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)")
	line := func(id int, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.edit","params":{"task":1,%s"request_id":%q}}`, id, params, rid)
	}
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }
	f := &fakeTeller{}
	r := call(t, mk("manager", f),
		line(2, ``, "m0"),
		line(3, `"title":"new title","priority":1,"time_box":"90m","body":"the new brief",`, "m1"),
		line(4, `"title":"new title",`, "m2"),
		line(5, `"owner":"nobody",`, "m3"),
		line(6, `"priority":4,`, "m4"),
		line(7, `"blocked_by":2,`, "m5"),
		line(8, `"blocked_by":2,"unblock":9,`, "m6"),
		line(9, `"unblock":2,`, "m7"),
		line(10, `"owner":"gp",`, "m9"),
		line(11, `"owner":"other",`, "m8"))
	if code(r[0]) != "bad_request" || code(r[3]) != "not_found" || code(r[4]) != "bad_request" || code(r[2]) != "refused" {
		t.Fatalf("%v", r)
	}
	want := "task T1 edited by manager: body changed, title 'old title' -> 'new title', priority 'P2' -> 'P1', timeout 'none' -> '1h30m, once it is doing'"
	if got := r[1]["result"].(map[string]any)["said"]; got != want {
		t.Fatalf("%v\nwant %s", got, want)
	}
	if r[5]["result"].(map[string]any)["said"] != "task T1 now waits on T2" {
		t.Fatalf("%v", r[5])
	}
	if code(r[6]) != "refused" || count(t, db, "SELECT COUNT(*) FROM events WHERE kind = 'block_task'") != 1 {
		t.Fatalf("a refused part undoes the earlier parts: the second block_task event is not kept: %v", r[6])
	}
	if r[7]["result"] == nil || len(f.edited) < 3 || f.edited[len(f.edited)-2].Freed != "worker" {
		t.Fatalf("clearing the last wait frees its owner: %v %+v", r[7], f.edited)
	}
	if why := r[8]["error"].(map[string]any)["message"]; code(r[8]) != "refused" || why != "T1 cannot go to gp: its brief is sent on manager's behalf, and denied: no edge declares manager -> gp (unlisted edges are denied by default)" {
		t.Fatalf("a move its brief cannot be delivered to is refused: %v", r[8])
	}
	if count(t, db, "SELECT COUNT(*) FROM events WHERE kind = 'edit_task' AND json_extract(payload, '$.owner.new') = 'gp'") != 0 ||
		count(t, db, "SELECT COUNT(*) FROM events WHERE kind = 'edit_task' AND json_extract(payload, '$.owner.new') = 'other'") != 1 {
		t.Fatal("the refused move changed the owner")
	}
	if r[9]["result"] == nil || f.edited[len(f.edited)-1].WasOwner != "worker" {
		t.Fatalf("a changed owner is handed to the Teller to be sent on: %v %+v", r[8], f.edited)
	}
	for q, wantN := range map[string]int{
		"SELECT COUNT(*) FROM tasks WHERE id = 1 AND title = 'new title' AND priority = 1 AND timeout_s = 5400 AND owner = 'other' AND edited_by = 'manager'":               1,
		"SELECT COUNT(*) FROM requests WHERE id = 10 AND body = 'the new brief'":                                                                                            1,
		"SELECT COUNT(*) FROM events WHERE kind = 'edit_task' AND json_extract(payload, '$.body.old') = 'the brief' AND json_extract(payload, '$.title.new') = 'new title'": 1,
	} {
		if got := count(t, db, q); got != wantN {
			t.Errorf("%s: %d, want %d", q, got, wantN)
		}
	}
	var ev string
	db.QueryRow("SELECT payload FROM events WHERE kind = 'edit_task' ORDER BY id LIMIT 1").Scan(&ev)
	if ev[:60] != `{"task_id": 1, "body": {"old": "the brief", "new": "the new ` {
		t.Fatalf("keys go out in the order Python wrote them: %s", ev)
	}
}

func TestAWaitChangeIsToldToTheLeadOnlyWhenTheBlockerIsHeldOutsideTheTeam(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'one', 'worker', 'manager', 1), (2, 'mate', 'other', 'manager', 1), "+
			"(3, 'outside', 'gp', 'manager', 1), (4, 'founder', 'human', 'manager', 1)")
	f := &fakeTeller{}
	line := func(id int, params string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.edit","params":{"task":1,%s,"request_id":"r%d"}}`, id, params, id)
	}
	r := call(t, mk("worker", f),
		line(2, `"blocked_by":2`), line(3, `"unblock":2`),
		line(4, `"blocked_by":3`), line(5, `"unblock":3`),
		line(6, `"blocked_by":4`))
	for i, x := range r {
		if x["result"] == nil {
			t.Fatalf("%d: %v", i, x)
		}
	}
	got := func(text string) int {
		return count(t, db, fmt.Sprintf("SELECT COUNT(*) FROM requests WHERE requester = 'task-board' AND target = 'manager' AND body = %q", text))
	}
	for text, want := range map[string]int{
		"task T1 now waits on T2":       0,
		"task T1 no longer waits on T2": 0,
		"task T1 now waits on T3":       1,
		"task T1 no longer waits on T3": 1,
		"task T1 now waits on T4":       1,
	} {
		if n := got(text); n != want {
			t.Errorf("%q told to the lead %d times, want %d", text, n, want)
		}
	}
}

func TestEditAllowsAnEmptyBody(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, priority) VALUES(1, 'old title', 'worker', 'manager', 1, 2)",
		"INSERT INTO requests(id, requester, target, body, state, kind, queue_order, ts) VALUES(10, 'manager', 'worker', 'the brief', 'queued', 'task', 10, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)")
	r := call(t, mk("manager", &fakeTeller{}), `{"jsonrpc":"2.0","id":2,"method":"task.edit","params":{"task":1,"body":"  ","request_id":"e1"}}`)
	if r[0]["result"] == nil || count(t, db, "SELECT COUNT(*) FROM requests WHERE id = 10 AND body = ''") != 1 {
		t.Fatalf("%v", r[0])
	}
}
