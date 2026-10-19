package reads

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// Every change to a task is a row of who, field, was, now and when, newest first, read from the events its writes keep: an edit is one row
// per field, a state row's was is the state before it, a memo keeps its text after it is deleted, and another task's events are not shown.
func TestTaskHistoryListsEveryChangeAsRowsNewestFirst(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'ship it', 'bob', 'amy', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES
		(10, 'amy', 'task_new', '{"task_id": 1, "request_id": 5, "owner": "al", "expects": "report"}'),
		(11, 'amy', 'queue_task', '{"task_id": 1, "after_request": 5}'),
		(12, 'cat', 'edit_task', '{"task_id": 1, "owner": {"old": "al", "new": "bob"}, "title": {"old": "ship", "new": "ship it"}}'),
		(13, 'amy', 'block_task', '{"task_id": 1, "blocks_on": 7}'),
		(14, 'bob', 'task_start', '{"task_id": 1, "was_current": null}'),
		(15, 'cat', 'memo_add', '{"task_id": 1, "memo": 3, "now": "mind the cache"}'),
		(16, 'cat', 'memo_edit', '{"task_id": 1, "memo": 3, "was": "mind the cache", "now": "mind the disk"}'),
		(17, 'human', 'memo_delete', '{"task_id": 1, "memo": 3, "was": "mind the disk"}'),
		(18, 'dan', 'unblock_task', '{"task_id": 1, "blocks_on": 7, "after_request": 6}'),
		(19, 'bob', 'mark_task_done', '{"task_id": 1, "done": "abc123", "from": "doing"}'),
		(20, 'cat', 'edit_task', '{"task_id": 2, "title": {"old": "x", "new": "y"}}')`)
	got, err := e.taskHistory(nil, map[string]any{"task": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, r := range got.(map[string]any)["rows"].([]any) {
		m := r.(map[string]any)
		at := strings.TrimSuffix(strings.TrimPrefix(fmt.Sprint(m["at"]), "1970-01-01T00:00:"), "Z")
		line := fmt.Sprintf("%s %s %s: %q -> %q", at, m["by"], m["field"], m["was"], m["now"])
		if m["note"] != nil {
			line += fmt.Sprintf(" (%v)", m["note"])
		}
		if m["memo"] != nil {
			line += fmt.Sprintf(" memo %v", m["memo"])
		}
		lines = append(lines, line)
	}
	want := []string{
		`19 bob state: "doing" -> "done" (abc123)`,
		`18 dan blocked_by: "T7" -> ""`,
		`17 human memo: "mind the disk" -> "" memo 3`,
		`16 cat memo: "mind the cache" -> "mind the disk" memo 3`,
		`15 cat memo: "" -> "mind the cache" memo 3`,
		`14 bob state: "queued" -> "doing"`,
		`13 amy blocked_by: "" -> "T7"`,
		`12 cat title: "ship" -> "ship it"`,
		`12 cat assignee: "al" -> "bob"`,
		`11 amy state: "opened" -> "queued"`,
		`10 amy state: "" -> "opened"`,
		`10 amy assignee: "" -> "al"`,
		`10 amy title: "" -> "ship"`,
	}
	if strings.Join(lines, "\n") != strings.Join(want, "\n") {
		t.Fatalf("got:\n%s\nwant:\n%s", strings.Join(lines, "\n"), strings.Join(want, "\n"))
	}
	if _, err := e.taskHistory(nil, map[string]any{"task": json.Number("9")}); err == nil || !strings.Contains(err.Error(), "no task T9") {
		t.Fatalf("a missing task: %v", err)
	}
}

// The founder's answer is a state row to answered, and the asker taking the task back is an assignee row under the asker that ends it.
func TestTaskHistoryShowsAnAnswerAndWhoTookTheTaskBack(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'merge it', 'boss', 'boss', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES
		(10, 'boss', 'task_new', '{"task_id": 1, "request_id": 5, "owner": "human", "expects": "decision"}'),
		(11, 'human', 'task_report', '{"task_id": 1, "report": "merged", "from": "queued", "to": "boss", "request_id": 6}'),
		(12, 'boss', 'task_handoff', '{"task_id": 1, "from": "human", "to": "boss", "note": "mine again", "request_id": 7}')`)
	got, err := e.taskHistory(nil, map[string]any{"task": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, r := range got.(map[string]any)["rows"].([]any) {
		m := r.(map[string]any)
		lines = append(lines, fmt.Sprintf("%v %s: %q -> %q", m["by"], m["field"], m["was"], m["now"]))
	}
	want := []string{
		`boss state: "review" -> "queued"`,
		`boss assignee: "human" -> "boss"`,
		`human state: "opened" -> "review"`,
		`boss state: "" -> "opened"`,
		`boss assignee: "" -> "human"`,
		`boss title: "" -> "merge it"`,
	}
	if strings.Join(lines, "\n") != strings.Join(want, "\n") {
		t.Fatalf("got:\n%s\nwant:\n%s", strings.Join(lines, "\n"), strings.Join(want, "\n"))
	}
}
