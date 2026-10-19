package taskwrites

import (
	"testing"
)

// A third party moves a task it neither holds nor asked, and task.edit's reassign clears an answer the same way.
func TestAnyoneChangesTheAssignee(t *testing.T) {
	sdb, _, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'a', 'w', 'boss', 1, 'boss'), (2, 'b', 'w', 'boss', 1, 'boss')",
	)
	if r := run("mgr", "task.handoff", "h1", `,"task":1,"to":"boss","note":"boss has it now"`); r["error"] != nil {
		t.Fatalf("%v", r["error"])
	}
	if r := run("mgr", "task.edit", "e1", `,"task":2,"owner":"boss"`); r["error"] != nil {
		t.Fatalf("%v", r["error"])
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE owner = 'boss'") != 2 {
		t.Fatal("both moved")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM events WHERE agent = 'mgr' AND kind IN ('task_handoff', 'edit_task')") != 2 {
		t.Fatal("each change is kept under mgr")
	}
}

// The founder's done closes their own task, and one someone else asked of them.
func TestTheFoundersDoneClosesTheirOwnTaskAndOneSomeoneElseAsked(t *testing.T) {
	sdb, _, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'mine', 'human', 'human', 1, 'human')",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(2, 'theirs', 'human', 'boss', 1, 'boss')",
	)
	for _, tid := range []string{"1", "2"} {
		if got := said(t, run("", "task.done", "d"+tid, `,"task":`+tid+`,"evidence":"closed"`)); got != "task T"+tid+" done (closed)" {
			t.Fatal(got)
		}
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE done_evidence = 'closed'") != 2 {
		t.Fatal("both closed")
	}
}
