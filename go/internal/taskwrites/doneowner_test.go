package taskwrites

import (
	"fmt"
	"testing"
)

// A task the founder holds is closed from under them by its parent's owner or reporter (or opener), or a lead of its team, never by a stranger,
// and the founder hears it.
func TestFounderHeldTaskIsClosedByItsParentOwnerOrALead(t *testing.T) {
	sdb, _, call := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, reporter, created_at, expects) VALUES(1, 'parent', 'mgr', 'boss', 'lead.auditor', 1, 'report')",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, expects) VALUES(2, 'parent no reporter', 'mgr', 'boss', 1, 'report')",
		"INSERT INTO tasks(id, title, owner, opened_by, parent_task, created_at, expects) VALUES(3, 'k', 'human', 'w', 1, 1, 'report'), (4, 'k', 'human', 'w', 1, 1, 'report'), "+
			"(5, 'k', 'human', 'w', 2, 1, 'report'), (6, 'k', 'human', 'w', NULL, 1, 'report'), (7, 'k', 'human', 'w', 1, 1, 'report')")
	n := func(q string) (c int) { sdb.QueryRow(q).Scan(&c); return }
	if r := call("boss", "task.done", "r0", `,"task":7,"evidence":"taken back"`); r["error"] == nil {
		t.Fatalf("a stranger closed a founder-held task: %v", r)
	}
	for _, c := range []struct {
		agent string
		task  int
	}{{"mgr", 3}, {"lead.auditor", 4}, {"boss", 5}, {"lead", 6}} {
		said(t, call(c.agent, "task.done", fmt.Sprint("r", c.task), fmt.Sprintf(`,"task":%d,"evidence":"taken back"`, c.task)))
		if n(fmt.Sprintf("SELECT COUNT(*) FROM requests WHERE target = 'human' AND body = 'T%d done by %s: taken back'", c.task, c.agent)) != 1 {
			t.Fatalf("the founder was not told in one line that %s closed T%d", c.agent, c.task)
		}
	}
}
