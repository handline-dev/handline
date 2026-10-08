package taskwrites

import (
	"fmt"
	"testing"
)

func TestNewOpensATaskAndAsksItsOwnerInOneTransaction(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'parent', 'manager', 'gp', 1), (2, 'blocker', 'other', 'manager', 1), "+
			"(3, 'gone', 'other', 'manager', 1)",
		"UPDATE tasks SET discarded_by = 'manager', discarded_at = 2 WHERE id = 3")
	line := func(id int, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.new","params":{%s"request_id":%q}}`, id, params, rid)
	}
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }
	base := `"owner":"worker","title":"build  it","body":"the work","time_box":"2h",`
	f := &fakeTeller{}
	r := call(t, mk("manager", f),
		line(2, base+`"priority":"p1","expects":"sha","parent":1,"blocked_by":2,`, "n1"),
		line(3, base+`"priority":"p1","expects":"sha","parent":1,"blocked_by":2,`, "n1"),
		line(4, `"owner":"worker","title":"t","time_box":"2h",`, "n2"),
		line(5, `"owner":"worker","title":"t","body":"b",`, "n3"),
		line(6, `"owner":"worker","title":"t","body":"b","time_box":"9h",`, "n4"),
		line(7, `"owner":"nobody","title":"t","body":"b","time_box":"1h",`, "n5"),
		line(8, base+`"blocked_by":3,`, "n6"),
		line(9, base+`"parent":99,`, "n7"),
		line(10, base+`"relation":"x",`, "n8"))
	res := r[0]["result"].(map[string]any)
	if res["task"].(float64) != 4 || res["said"] != "task T4 opened for worker (P1, expects sha, timeout 2h00m): queued as #1 for worker" {
		t.Fatalf("%v", r[0])
	}
	if r[1]["result"].(map[string]any)["replayed"] != true || len(f.opened) != 1 || f.opened[0] != "T4 manager worker build  it" && f.opened[0] != "T4 manager worker build it" {
		t.Fatalf("a retry replays and opens nothing twice: %v %v", r[1], f.opened)
	}
	wants := []string{"refused", "bad_request", "bad_request", "not_found", "refused", "not_found", "bad_request"}
	for i, w := range wants {
		if got := code(r[2+i]); got != w {
			t.Errorf("call %d: %v, want %s: %v", 4+i, got, w, r[2+i])
		}
	}
	for q, want := range map[string]int{
		"SELECT COUNT(*) FROM tasks": 4,
		"SELECT COUNT(*) FROM tasks WHERE id = 4 AND title = 'build  it' AND owner = 'worker' AND opened_by = 'manager' AND parent_task = 1 AND priority = 1 AND expects = 'sha' AND timeout_s = 7200 AND timeout_at IS NULL": 1,
		"SELECT COUNT(*) FROM task_requests WHERE task_id = 4":                                                      1,
		"SELECT COUNT(*) FROM agent_state WHERE agent = 'worker' AND current_task = 4":                              1,
		"SELECT COUNT(*) FROM task_blocks WHERE (task_id = 1 AND blocks_on = 4) OR (task_id = 4 AND blocks_on = 2)": 2,
		"SELECT COUNT(*) FROM events WHERE kind = 'task_new' AND json_extract(payload, '$.owner') = 'worker'":       1,
	} {
		if got := count(t, db, q); got != want {
			t.Errorf("%s: %d, want %d", q, got, want)
		}
	}

	r = call(t, mk("human"), line(2, base, "f1"))
	if r[0]["error"] == nil {
		t.Fatalf("a task is opened by an agent: %v", r[0])
	}
	f = &fakeTeller{deny: "no edge"}
	r = call(t, mk("manager", f), line(2, base, "d1"))
	if code(r[0]) != "refused" || count(t, db, "SELECT COUNT(*) FROM tasks") != 4 || count(t, db, "SELECT COUNT(*) FROM requests WHERE state = 'denied'") != 1 || len(f.opened) != 0 {
		t.Fatalf("a refused owner leaves no task behind, only the denial: %v", r[0])
	}
}
