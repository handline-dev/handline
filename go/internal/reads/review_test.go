package reads

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A task in review says who it waits on: its assignee's lead, then, accepted with a PR, the founder's merge OK.
func TestATaskInReviewWaitsOnTheLeadThenOnTheMerge(t *testing.T) {
	e := testEnv(t)
	if err := os.WriteFile(filepath.Join(e.Home, "team.yaml"), []byte("teams:\n  lead: [lead, w]\nagents:\n  lead: {}\n  w: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, reported_at, report, report_via, pr) VALUES(1, 'a', 'w', 'human', 1, 2, 'built', 'reason', 12)")
	review := func() (map[string]any, map[string]any) {
		t.Helper()
		rows, err := query(e.DB, "SELECT * FROM tasks WHERE id = 1")
		if err != nil {
			t.Fatal(err)
		}
		task, err := e.taskOf(rows[0], "review")
		if err != nil {
			t.Fatal(err)
		}
		rep, _ := task["report"].(map[string]any)
		return task, rep
	}
	text := func(task map[string]any) any { return task["status"].(map[string]any)["text"] }
	if task, rep := review(); rep["waits_on"] != "lead" || rep["accepted"] != nil || task["pr"] != int64(12) || text(task) != "review: waits on lead to accept it" {
		t.Fatalf("before the accept: pr %v report %v status %v", task["pr"], rep, task["status"])
	}
	run(t, e, "UPDATE tasks SET accepted_by = 'lead', accepted_at = 3 WHERE id = 1")
	if task, rep := review(); rep["waits_on"] != "human" || rep["accepted"].(map[string]any)["by"] != "lead" || text(task) != "accepted: waits on PR #12 to merge" {
		t.Fatalf("after the accept: %v status %v", rep, task["status"])
	}
}

// The history's state rows say who moved the task: the lead's accept of a PR task leaves it in review, the merge closes it, and a handoff
// that drops an answer in review sends it back to queued.
func TestTheHistorySaysWhoMovedATaskThroughReview(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'w', 'human', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES
		(10, 'human', 'task_new', '{"task_id": 1, "owner": "w"}'),
		(11, 'w', 'task_report', '{"task_id": 1, "report": "half"}'),
		(12, 'w', 'task_handoff', '{"task_id": 1, "from": "w", "to": "v"}'),
		(13, 'v', 'edit_task', '{"task_id": 1, "pr": {"old": "", "new": "#12"}}'),
		(14, 'v', 'task_report', '{"task_id": 1, "report": "built"}'),
		(15, 'lead', 'accept_task', '{"task_id": 1, "done": "built", "pr": 12}'),
		(16, 'lead', 'mark_task_done', '{"task_id": 1, "done": "abc123"}')`)
	got, err := e.taskHistory(nil, map[string]any{"task": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, r := range got.(map[string]any)["rows"].([]any) {
		m := r.(map[string]any)
		if m["field"] == "state" || m["field"] == "pr" {
			lines = append(lines, fmt.Sprintf("%s %s: %v -> %v", m["by"], m["field"], m["was"], m["now"]))
		}
	}
	want := []string{
		"lead state: review -> done",
		"lead state: review -> review",
		"v state: queued -> review",
		"v pr:  -> #12",
		"w state: review -> queued",
		"w state: opened -> review",
		"human state:  -> opened",
	}
	if strings.Join(lines, "\n") != strings.Join(want, "\n") {
		t.Fatalf("got:\n%s\nwant:\n%s", strings.Join(lines, "\n"), strings.Join(want, "\n"))
	}
}
