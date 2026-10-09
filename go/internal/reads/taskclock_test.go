package reads

import (
	"strconv"
	"testing"
	"time"
)

func clockOf(t *testing.T, e *Env, id int64) (map[string]any, map[string]any) {
	t.Helper()
	got, err := e.Tasks([]int64{id})
	if err != nil {
		t.Fatal(err)
	}
	c, _ := got[id]["time"].(map[string]any)
	return got[id], c
}

func TestATasksClockRunsOnlyWhileItIsDoing(t *testing.T) {
	e := testEnv(t)
	now := time.Now().Unix()
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'q', 'bob', 'human', 1, 'human')")
	_, c := clockOf(t, e, 1)
	if c["length_s"] != int64(defaultTaskTimeoutS) || c["default"] != true || c["start"] != nil || c["end"] != nil || c["told"] != 0 {
		t.Fatalf("a queued task has a length and no counting: %v", c)
	}
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, timeout_s, timeout_at, sent_at) VALUES(2, 'd', 'bob', 'human', 1, 'human', 600, ?, 1)", now+300)
	run(t, e, "INSERT INTO agent_state(agent, state, updated, current_task) VALUES('bob', 'RUNNING', 0, 2)")
	task, c := clockOf(t, e, 2)
	if task["state"] != "doing" || c["length_s"] != int64(600) || c["default"] != false || c["start"] != ts(now+300-600) ||
		c["end"] != ts(now+300) || c["overdue_since"] != nil {
		t.Fatalf("%v %v", task["state"], c)
	}
	run(t, e, "UPDATE tasks SET done_evidence = 'x' WHERE id = 2")
	if task, c = clockOf(t, e, 2); task["time"] != nil {
		t.Fatalf("a done task has no clock: %v", task["time"])
	}
}

func TestAnOverdueTasksClockSaysSinceTheFirstMissedDeadline(t *testing.T) {
	e := testEnv(t)
	now := time.Now().Unix()
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, timeout_s, timeout_at, sent_at) VALUES(1, 'd', 'bob', 'human', 1, 'human', 600, ?, 1)", now-100)
	run(t, e, "INSERT INTO agent_state(agent, state, updated, current_task) VALUES('bob', 'RUNNING', 0, 1)")
	if _, c := clockOf(t, e, 1); c["overdue_since"] != ts(now-100) || c["told"] != 0 {
		t.Fatalf("no notice yet: the deadline is the first miss: %v", c)
	}
	// Two notices moved the deadline twice: 1000 -> now-700 -> now-100. A third, from an earlier edit, is not in the chain.
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES
		(1, 'human', 'task_timeout_exceeded', '{"task_id": 1, "deadline": 5, "next": 7}'),
		(2, 'human', 'task_timeout_exceeded', '{"task_id": 1, "deadline": 1000, "next": `+itoa(now-700)+`}'),
		(3, 'human', 'task_timeout_exceeded', '{"task_id": 1, "deadline": `+itoa(now-700)+`, "next": `+itoa(now-100)+`}')`)
	if _, c := clockOf(t, e, 1); c["overdue_since"] != ts(int64(1000)) || c["told"] != 2 {
		t.Fatalf("the chain of notices leads back to the first miss: %v", c)
	}
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }
