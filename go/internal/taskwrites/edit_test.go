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
