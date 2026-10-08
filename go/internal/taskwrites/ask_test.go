package taskwrites

import (
	"fmt"
	"strings"
	"testing"
)

func TestAskPutsTheFoundersQuestionToAnAgentOnTheTaskWithoutMovingIt(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'worker', 'manager', 1)")
	line := func(id int, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.ask","params":{%s"request_id":%q}}`, id, params, rid)
	}
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }
	f := &fakeTeller{}
	r := call(t, mk("", f),
		line(2, `"task":1,"to":"other","text":"  why?  ",`, "a1"),
		line(3, `"task":1,"to":"other","text":"  why?  ",`, "a1"),
		line(4, `"task":9,"to":"other","text":"x",`, "a2"),
		line(5, `"task":1,"to":"nobody","text":"x",`, "a3"),
		line(6, `"task":1,"to":"other","text":" ",`, "a4"),
		line(7, `"task":"1","to":"other","text":"x",`, "a5"),
		line(8, `"task":1,"to":"","text":"x",`, "a6"))
	res := r[0]["result"].(map[string]any)
	if res["request"].(float64) != 1 || res["said"] != "queued as #1 for other" {
		t.Fatalf("%v", r[0])
	}
	if r[1]["result"].(map[string]any)["replayed"] != true {
		t.Fatalf("a retry replays: %v", r[1])
	}
	if code(r[2]) != "not_found" || code(r[3]) != "not_found" || code(r[4]) != "bad_request" || code(r[5]) != "bad_request" || code(r[6]) != "bad_request" {
		t.Fatalf("%v", r[2:])
	}
	var body string
	db.QueryRow("SELECT body FROM requests WHERE id = 1").Scan(&body)
	if !strings.HasPrefix(body, "Question on T1 from the founder (the task keeps its holder): why?\n\nAnswer by replying") {
		t.Fatalf("%q", body)
	}
	for q, want := range map[string]int{
		"SELECT COUNT(*) FROM task_requests WHERE task_id = 1 AND request_id = 1":                    1,
		"SELECT COUNT(*) FROM events WHERE kind = 'task_question' AND dedup_key = 'task_question:1'": 1,
		"SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'worker'":                               1,
	} {
		if got := count(t, db, q); got != want {
			t.Errorf("%s: %d, want %d", q, got, want)
		}
	}
	r = call(t, mk("worker", f), line(2, `"task":1,"to":"other","text":"x",`, "b1"))
	if r[0]["error"] == nil {
		t.Fatalf("only the founder asks: %v", r[0])
	}
	f = &fakeTeller{deny: "no edge"}
	r = call(t, mk("", f), line(2, `"task":1,"to":"other","text":"x",`, "c1"))
	if code(r[0]) != "refused" {
		t.Fatalf("%v", r[0])
	}
}
