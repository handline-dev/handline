package taskwrites

import (
	"fmt"
	"strings"
	"testing"
)

func requestLine(method string, id int, params, rid string) string {
	return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":%q,"params":{%s"request_id":%q}}`, id, method, params, rid)
}

func TestARequestIsATaskTheFounderOpenedFromTheirMessage(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db, "INSERT INTO requests(id, requester, target, body, state, kind, ts) VALUES"+
		"(1, 'human', 'manager', 'please do the thing"+"\nwith detail', 'landed', 'say', 1), (2, 'human', 'manager', 'second', 'landed', 'say', 1), "+
		"(3, 'worker', 'manager', 'not the founder', 'landed', 'say', 1), (4, 'human', 'worker', 'for someone else', 'landed', 'say', 1)")
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }

	r := call(t, mk("manager"),
		requestLine("task.request", 2, `"request":1,`, "r1"),
		requestLine("task.request", 3, `"request":1,"title":"other",`, "r2"),
		requestLine("task.request", 4, `"request":3,`, "r3"),
		requestLine("task.request", 5, `"request":4,`, "r4"),
		requestLine("task.request", 6, `"request":9,`, "r5"),
		requestLine("task.request", 7, `"request":2,"title":"Named","owner":"nobody",`, "r6"))
	if res := r[0]["result"].(map[string]any); res["task"].(float64) != 1 {
		t.Fatalf("%v", r[0])
	}
	if res := r[1]["result"].(map[string]any); res["task"].(float64) != 1 || res["existing"] != true {
		t.Fatalf("a message converts once: %v", r[1])
	}
	for i, want := range []string{"refused", "refused", "refused", "not_found"} {
		if got := code(r[2+i]); got != want {
			t.Errorf("call %d: %v, want %s: %v", 4+i, got, want, r[2+i])
		}
	}
	for q, want := range map[string]int{
		"SELECT COUNT(*) FROM tasks": 1,
		"SELECT COUNT(*) FROM tasks WHERE id = 1 AND title = 'please do the thing' AND owner = 'manager' AND opened_by = 'human' AND from_request = 1": 1,
		"SELECT COUNT(*) FROM task_requests WHERE task_id = 1 AND request_id = 1":                                                                      1,
		"SELECT COUNT(*) FROM task_memos WHERE task_id = 1 AND author = 'human' AND body LIKE 'please do the thing%'":                                  1,
		"SELECT COUNT(*) FROM agent_state WHERE agent = 'manager' AND current_task = 1":                                                                1,
	} {
		if got := count(t, db, q); got != want {
			t.Errorf("%s: %d, want %d", q, got, want)
		}
	}

	// The founder converts a message of their own to the lead, who is then given a title and owner of their choosing.
	r = call(t, mk(""), requestLine("task.request", 2, `"request":2,"title":"Named","owner":"worker",`, "f1"))
	if r[0]["error"] != nil || count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 2 AND title = 'Named' AND owner = 'worker' AND opened_by = 'human' AND from_request = 2") != 1 {
		t.Fatalf("%v", r[0])
	}
}

func TestNoWorkMarksAMessageUntilItIsMadeARequest(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db, "INSERT INTO requests(id, requester, target, body, state, kind, ts) VALUES"+
		"(1, 'human', 'manager', 'thanks', 'landed', 'say', 1), (2, 'human', 'manager', 'do this', 'landed', 'say', 1), (3, 'human', 'worker', 'other', 'landed', 'say', 1)")
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }
	r := call(t, mk("manager"),
		requestLine("task.none", 2, `"request":1,"reason":"just thanks",`, "n1"),
		requestLine("task.none", 3, `"request":3,"reason":"x",`, "n2"),
		requestLine("task.none", 4, `"request":1,"reason":"  ",`, "n3"),
		requestLine("task.request", 5, `"request":2,`, "n4"),
		requestLine("task.none", 6, `"request":2,"reason":"changed my mind",`, "n5"),
		requestLine("task.none", 7, `"request":1,"reason":"just thanks",`, "n6"))
	if r[0]["error"] != nil || count(t, db, "SELECT COUNT(*) FROM requests WHERE id = 1 AND no_work = 'just thanks' AND no_work_by = 'manager'") != 1 {
		t.Fatalf("%v", r[0])
	}
	if code(r[1]) != "refused" || code(r[2]) != "bad_request" || code(r[4]) != "refused" ||
		!strings.Contains(r[4]["error"].(map[string]any)["message"].(string), "already T") {
		t.Fatalf("a message sent to another agent, a blank reason, a message already a request: %v %v %v", r[1], r[2], r[4])
	}
	r = call(t, mk("manager"), requestLine("task.request", 2, `"request":1,`, "n7"))
	if r[0]["error"] != nil || count(t, db, "SELECT COUNT(*) FROM requests WHERE id = 1 AND no_work IS NULL AND no_work_by IS NULL") != 1 {
		t.Fatalf("making it a request clears the mark: %v", r[0])
	}
}
