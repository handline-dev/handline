package reads

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
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

func TestATasksPageNamesItsSubtasksParentMergeAndWhomToAsk(t *testing.T) {
	e := testEnv(t)
	for _, a := range []string{"amy", "bob", "cy", "dee", "eve"} {
		addAgent(t, e, a, "")
	}
	os.WriteFile(filepath.Join(e.Home, "team.yaml"), []byte("teams:\n  eng: [eve, bob, cy]\nagents:\n  bob: {}\n  cy: {}\n  eve: {}\nedges:\n"), 0o644)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'the story', 'amy', 'amy', 10), "+
		"(2, 'a', 'bob', 'amy', 10), (3, 'b', 'cy', 'bob', 10), (4, 'c', 'cy', 'bob', 10)")
	run(t, e, "UPDATE tasks SET parent_task = 1 WHERE id = 2")
	run(t, e, "UPDATE tasks SET parent_task = 2 WHERE id IN (3, 4)")
	run(t, e, "UPDATE tasks SET done_evidence = 'sha', done_at = 20 WHERE id = 3")
	run(t, e, "UPDATE tasks SET discarded_by = 'bob', discarded_at = 20, merged_into = 3 WHERE id = 4")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, edge_ok) VALUES(1, 1, 'human', 'cy', 'x', 'landed', 1), "+
		"(2, 2, 'dee', 'human', 'y', 'landed', 1), (3, 3, 'human', 'cy', 'z', 'landed', 1)")
	got, err := e.taskGet(nil, map[string]any{"task": json.Number("2")})
	if err != nil {
		t.Fatal(err)
	}
	d := got.(map[string]any)
	subs, _ := json.Marshal(d["subtasks"])
	if want := `[{"id":3,"owner":"cy","state":"done","title":"b"},{"id":4,"owner":"cy","state":"discarded","title":"c"}]`; string(subs) != want {
		t.Fatalf("subtasks %s, want %s", subs, want)
	}
	if d["parent_title"] != "the story" || d["merged_into"] != nil {
		t.Fatalf("parent_title %v, merged_into %v", d["parent_title"], d["merged_into"])
	}
	if people := fmt.Sprint(d["people"]); people != "map[others:[amy] recent:[cy dee] team:[eve bob]]" {
		t.Fatalf("people %s", people)
	}
	if got, _ = e.taskGet(nil, map[string]any{"task": json.Number("4")}); got.(map[string]any)["merged_into"] != int64(3) {
		t.Fatalf("merged_into %v", got.(map[string]any)["merged_into"])
	}
}
