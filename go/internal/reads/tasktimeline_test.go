package reads

import (
	"encoding/json"
	"fmt"
	"testing"
)

func TestATasksTimelineIsEverythingThatHappenedOnItInOrder(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'amy', 10)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 10, 'amy', 'bob', 'the brief\nmore', 'landed', 'task', 1), "+
		"(2, 30, 'amy', 'cy', 'T1 handed to you', 'landed', 'task', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1), (1, 2)")
	run(t, e, "INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'amy', 'a memo', 20.5)")
	for _, ev := range []struct {
		ts            int
		who, kind, at string
	}{
		{20, "amy", "edit_task", `"body": {"old": "the brief", "new": "a new brief"}`},
		{30, "amy", "task_handoff", `"from": "bob", "to": "cy", "note": "yours", "request_id": 2, "at": 30.2`},
		{40, "cy", "mark_task_done", `"from": "doing"`},
		{50, "amy", "reject_task", `"note": "not yet"`},
	} {
		run(t, e, "INSERT INTO events(ts, agent, kind, payload) VALUES(?, ?, ?, ?)", ev.ts, ev.who, ev.kind, `{"task_id": 1, `+ev.at+`}`)
	}
	got, err := e.taskGet(nil, map[string]any{"task": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	var rows []string
	for _, r := range got.(map[string]any)["timeline"].([]any) {
		m := r.(map[string]any)
		rows = append(rows, fmt.Sprintf("%v|%v|%v", m["kind"], m["who"], m["text"]))
	}
	want := []string{"request|amy → bob|#1  landed  the brief", "edit|amy|a new brief", "memo|amy|a memo", "handoff|bob → cy|yours",
		"state|cy|doing -> done", "reject|amy|not yet"}
	if fmt.Sprint(rows) != fmt.Sprint(want) {
		t.Fatalf("got  %q\nwant %q", rows, want)
	}
}
