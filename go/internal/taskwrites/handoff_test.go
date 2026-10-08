package taskwrites

import (
	"fmt"
	"testing"
)

func TestHandoffMovesTheTaskToItsNewOwnerWithOneRequestAndRetiresTheOldOnes(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(1, 'build', 'worker', 'manager', 1, 1), (2, 'wait', 'other', 'manager', 1, NULL)",
		"INSERT INTO requests(id, requester, target, body, state, kind, queue_order, ts) VALUES(10, 'manager', 'worker', 'build', 'queued', 'task', 10, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)",
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('worker', 'IDLE', 1, 1)",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(2, 1, 1)")
	line := func(id int, task int, to, note, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.handoff","params":{"task":%d,"to":%q,"note":%q,"request_id":%q}}`, id, task, to, note, rid)
	}
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }
	f := &fakeTeller{}
	r := call(t, mk("other", f), line(2, 1, "manager", "x", "o1"))
	if code(r[0]) != "refused" {
		t.Fatalf("only the owner hands it on: %v", r[0])
	}
	r = call(t, mk("worker", f),
		line(2, 1, "nobody", "x", "w1"), line(3, 1, "worker", "x", "w2"), line(4, 1, "other", "  ", "w3"), line(5, 9, "other", "x", "w4"))
	if code(r[0]) != "not_found" || code(r[1]) != "refused" || code(r[2]) != "bad_request" || code(r[3]) != "not_found" {
		t.Fatalf("%v", r)
	}
	if len(f.handed) != 0 || count(t, db, "SELECT COUNT(*) FROM requests") != 1 {
		t.Fatal("a refused handoff sends nothing")
	}

	r = call(t, mk("worker", f), line(2, 1, "other", "built; test it", "h1"), line(3, 1, "other", "built; test it", "h1"))
	res := r[0]["result"].(map[string]any)
	if res["said"] != "T1 handed to other: queued as #11 for other\npushed" {
		t.Fatalf("%v", r[0])
	}
	if r[1]["result"].(map[string]any)["replayed"] != true || len(f.handed) != 1 {
		t.Fatalf("a retry replays and hands nothing twice: %v", r[1])
	}
	h := f.handed[0]
	if h.Task != 1 || h.RID != 11 || h.From != "worker" || h.To != "other" || !h.WasCurrent || len(h.Retired) != 1 {
		t.Fatalf("%+v", h)
	}
	for q, want := range map[string]int{
		"SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'other' AND edited_by = 'worker' AND sent_at IS NOT NULL":   1,
		"SELECT COUNT(*) FROM requests WHERE id = 10 AND state <> 'queued' AND queue_order IS NULL":                      1,
		"SELECT COUNT(*) FROM task_requests WHERE task_id = 1 AND request_id = 11":                                       1,
		"SELECT COUNT(*) FROM requests WHERE id = 11 AND send_now IS NOT NULL":                                           1,
		"SELECT COUNT(*) FROM agent_state WHERE agent = 'worker' AND current_task IS NULL":                               1,
		"SELECT COUNT(*) FROM agent_state WHERE agent = 'other' AND current_task = 1":                                    1,
		"SELECT COUNT(*) FROM task_blocks WHERE task_id = 2 AND blocks_on = 1 AND dormant = 1":                           1,
		"SELECT COUNT(*) FROM events WHERE kind = 'dormant_block'":                                                       1,
		"SELECT COUNT(*) FROM events WHERE kind = 'task_handoff' AND json_extract(payload, '$.note') = 'built; test it'": 1,
	} {
		if got := count(t, db, q); got != want {
			t.Errorf("%s: %d, want %d", q, got, want)
		}
	}

	f = &fakeTeller{deny: "no edge from other to worker"}
	r = call(t, mk("other", f), line(2, 1, "worker", "back", "d1"))
	if code(r[0]) != "refused" || count(t, db, "SELECT COUNT(*) FROM requests WHERE state = 'denied'") != 1 || count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'other'") != 1 {
		t.Fatalf("a denied ask leaves its denial row and the task where it was: %v", r[0])
	}
}
