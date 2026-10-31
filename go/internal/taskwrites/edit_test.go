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

// A done task's brief and result can both be rewritten, and a task that is not done has no result to edit.
func TestADoneTaskKeepsItsBriefAndResultEditable(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, done_evidence, done_at, done_via) VALUES(1, 'closed', 'worker', 'manager', 1, 'FAIL PR 7', 5, 'reason')",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(2, 'open', 'worker', 'manager', 1)",
		"INSERT INTO requests(id, requester, target, body, state, kind, queue_order, ts) VALUES(10, 'manager', 'worker', 'the brief', 'queued', 'task', 10, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)")
	edit := func(who string, id int, params, rid string) map[string]any {
		return call(t, mk(who, &fakeTeller{}), fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.edit","params":{%s,"request_id":%q}}`, id, params, rid))[0]
	}
	if r := edit("", 2, `"task":1,"body":"new brief","evidence":"PASS PR 7"`, "d1"); r["result"] == nil {
		t.Fatalf("the founder edits the brief and the result: %v", r)
	}
	for name, r := range map[string]map[string]any{
		"an empty result would reopen the task": edit("", 3, `"task":1,"evidence":""`, "d2"),
		"so would a blank one":                  edit("", 4, `"task":1,"evidence":"  \n"`, "d3"),
		"an open task has no result":            edit("", 5, `"task":2,"evidence":"early"`, "d4"),
		"a stranger may not rewrite a verdict":  edit("other", 6, `"task":1,"evidence":"PASS PR 8"`, "d5"),
		"nor the assignee":                      edit("worker", 7, `"task":1,"evidence":"PASS PR 8"`, "d6"),
		"nor the owner":                         edit("manager", 8, `"task":1,"evidence":"PASS PR 8"`, "d7"),
	} {
		if r["error"] == nil {
			t.Errorf("%s: accepted: %v", name, r)
		}
	}
	for q, want := range map[string]int{
		"SELECT COUNT(*) FROM requests WHERE id = 10 AND body = 'new brief'":                                             1,
		"SELECT COUNT(*) FROM tasks WHERE id = 1 AND done_evidence = 'PASS PR 7' AND done_at = 5":                        1, // still done, with the one edit
		"SELECT COUNT(*) FROM events WHERE kind = 'edit_task' AND json_extract(payload, '$.evidence.old') = 'FAIL PR 7'": 1,
		"SELECT COUNT(*) FROM events WHERE kind = 'edit_task'":                                                           1,
		"SELECT COUNT(*) FROM tasks WHERE id = 2 AND done_evidence IS NULL":                                              1,
	} {
		if n := count(t, db, q); n != want {
			t.Errorf("%s = %d, want %d", q, n, want)
		}
	}
}

func TestTheBriefOfADiscardedTaskIsEditable(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, discarded_by) VALUES(1, 'gone', 'worker', 'manager', 1, 'manager')",
		"INSERT INTO requests(id, requester, target, body, state, kind, queue_order, ts) VALUES(10, 'manager', 'worker', 'the brief', 'queued', 'task', 10, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)")
	r := call(t, mk("manager", &fakeTeller{}), `{"jsonrpc":"2.0","id":2,"method":"task.edit","params":{"task":1,"body":"better words","request_id":"g1"}}`)
	if r[0]["result"] == nil || count(t, db, "SELECT COUNT(*) FROM requests WHERE id = 10 AND body = 'better words'") != 1 {
		t.Fatalf("%v", r[0])
	}
}

func TestAnEstimateIsStoryPointsSetWhenATaskOpensChangedAndCleared(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'one', 'worker', 'manager', 1)")
	line := func(id int, method, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":%q,"params":{%s"request_id":%q}}`, id, method, params, rid)
	}
	f := &fakeTeller{}
	r := call(t, mk("manager", f),
		line(2, "task.edit", `"task":1,"estimate":4,`, "e1"),
		line(3, "task.edit", `"task":1,"estimate":5,`, "e2"),
		line(4, "task.edit", `"task":1,"estimate":5,`, "e3"),
		line(5, "task.edit", `"task":1,"estimate":null,`, "e4"),
		line(6, "task.new", `"owner":"worker","title":"sized","time_box":"1h","estimate":8,`, "e5"))
	if d := r[0]["error"].(map[string]any)["data"].(map[string]any); d["code"] != "bad_request" || d["field"] != "estimate" {
		t.Fatalf("4 is not a story point: %v", r[0])
	}
	if got := r[1]["result"].(map[string]any)["said"]; got != "task T1 edited by manager: estimate '' -> '5 points'" {
		t.Fatalf("%v", got)
	}
	if r[2]["error"] == nil {
		t.Fatalf("the same estimate again changes nothing: %v", r[2])
	}
	if got := r[3]["result"].(map[string]any)["said"]; got != "task T1 edited by manager: estimate '5 points' -> ''" ||
		count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND estimate IS NULL") != 1 {
		t.Fatalf("null clears it: %v", got)
	}
	if r[4]["result"] == nil || count(t, db, "SELECT COUNT(*) FROM tasks WHERE title = 'sized' AND estimate = 8") != 1 {
		t.Fatalf("task.new takes an estimate: %v", r[4])
	}
}

func TestAWaitOnACurrentTaskLetsItsOwnerGoInTheSameWrite(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'held', 'worker', 'manager', 1), (2, 'dep', 'other', 'manager', 1), "+
			"(3, 'queued', 'worker', 'manager', 1), (4, 'lead', 'manager', 'gp', 1)",
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('worker', 'IDLE', 1, 1), ('manager', 'IDLE', 1, 4)")
	f := &fakeTeller{}
	edit := func(id int, params string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.edit","params":{%s,"request_id":"e%d"}}`, id, params, id)
	}
	r := call(t, mk("manager", f), edit(2, `"task":3,"blocked_by":2`), edit(3, `"task":1,"blocked_by":2`))
	for i, x := range r {
		if x["result"] == nil {
			t.Fatalf("%d: %v", i, x)
		}
	}
	if len(f.edited) != 2 || f.edited[0].Freed != "" || f.edited[1].Freed != "worker" {
		t.Fatalf("a wait on a task nobody holds frees no one; one on the current task frees its owner: %+v", f.edited)
	}
	if n := count(t, db, "SELECT COUNT(*) FROM agent_state WHERE agent = 'worker' AND current_task IS NULL"); n != 1 {
		t.Fatal("the blocked task is still current")
	}
	if e := errText(call(t, mk("manager", f), `{"jsonrpc":"2.0","id":5,"method":"task.release","params":{"task":1,"request_id":"x1"}}`)[0]); e != "task T1 is draft, not doing - nothing to release" {
		t.Fatalf("the writes read a blocked task as not doing, as the board does: %q", e)
	}
	// a child under the lead's current task frees the lead
	f = &fakeTeller{}
	r = call(t, mk("manager", f), `{"jsonrpc":"2.0","id":4,"method":"task.new","params":{"owner":"other","title":"review it","body":"b","parent":4,"request_id":"n1"}}`)
	if r[0]["result"] == nil || len(f.edited) != 1 || f.edited[0].Freed != "manager" {
		t.Fatalf("%v %+v", r[0], f.edited)
	}
	if n := count(t, db, "SELECT COUNT(*) FROM agent_state WHERE agent = 'manager' AND current_task IS NULL"); n != 1 {
		t.Fatal("the parent is still its owner's current task")
	}
}
