package taskwrites

import (
	"fmt"
	"testing"
)

// A task started as its owner's own (task_self_start, no task_begin) is doing, so reopening it restarts its clock;
// the same task with no begin event is not, and its clock stays stopped.
func TestASelfStartedTaskIsDoingWhenReopened(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, done_evidence, done_at, done_via, timeout_s) VALUES"+
			"(1, 'self', 'manager', 'manager', 1, 'x', 2, 'reason', 600), (2, 'plain', 'manager', 'manager', 1, 'x', 2, 'reason', 600)",
		`INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'manager', 'task_self_start', '{"task_id": 1}')`)
	for _, tid := range []int{1, 2} {
		r := call(t, mk("manager"), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.reopen","params":{"task":%d,"note":"again","request_id":"r%d"}}`, tid, tid))
		if r[0]["error"] != nil {
			t.Fatalf("T%d: %v", tid, r[0])
		}
	}
	if n := count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND timeout_at IS NOT NULL"); n != 1 {
		t.Fatal("a self-started task reads doing: its clock restarts on reopen")
	}
	if n := count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 2 AND timeout_at IS NULL"); n != 1 {
		t.Fatal("a task with no begin event and nothing sent is not doing: its clock stays stopped")
	}
}
