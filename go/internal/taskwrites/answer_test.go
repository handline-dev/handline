package taskwrites

import (
	"strings"
	"testing"
)

// The founder's done on a task they hold that boss asked leaves it answered and with the founder; boss, told how, takes it back.
func TestTheFoundersDoneOnATaskSomeoneElseAskedLeavesItAnsweredWithThem(t *testing.T) {
	sdb, f, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'merge it', 'human', 'boss', 1, 'boss')",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body, landed_at) VALUES(10, 'boss', 'human', 'task', 'landed', 1, 'merge it', 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)",
	)
	if got := said(t, run("", "task.done", "d1", `,"task":1,"evidence":"merged"`)); got != "task T1 answered (merged); it stays with human until boss accepts it or takes it back" {
		t.Fatal(got)
	}
	if st := taskState(t, sdb, 1); st != "answered" {
		t.Fatalf("state %s", st)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'human' AND report = 'merged' AND done_evidence IS NULL") != 1 {
		t.Fatal("the task stays with the founder, the answer kept, not done")
	}
	if len(f.told) != 1 || f.told[0].To != "boss" || !strings.HasPrefix(f.told[0].Text, "T1 answered by the founder: merged - accept it") ||
		!strings.Contains(f.told[0].Text, "take it back (`hl task handoff T1 boss") {
		t.Fatalf("%+v", f.told)
	}
	// boss is not its owner and takes it back all the same: the answer goes with the move, the history keeps who moved it
	if r := run("boss", "task.handoff", "h1", `,"task":1,"to":"boss","note":"thanks, mine again"`); r["error"] != nil {
		t.Fatalf("anyone reassigns: %v", r["error"])
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'boss' AND reported_at IS NULL AND report IS NULL") != 1 {
		t.Fatal("the reassign clears the answer")
	}
	if st := taskState(t, sdb, 1); st == "answered" {
		t.Fatal("a task taken back is no longer answered")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM events WHERE kind = 'task_handoff' AND agent = 'boss' AND json_extract(payload, '$.from') = 'human'") != 1 {
		t.Fatal("the handoff is recorded under who made it")
	}
}

// A third party moves a task it neither holds nor asked, and task.edit's reassign clears an answer the same way.
func TestAnyoneChangesTheAssigneeAndTheChangeClearsAnAnswer(t *testing.T) {
	sdb, _, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, reported_at, report, report_via) "+
			"VALUES(1, 'a', 'w', 'boss', 1, 'boss', 5, 'built', 'reason'), (2, 'b', 'w', 'boss', 1, 'boss', 5, 'built', 'reason')",
	)
	if r := run("mgr", "task.handoff", "h1", `,"task":1,"to":"boss","note":"boss has it now"`); r["error"] != nil {
		t.Fatalf("%v", r["error"])
	}
	if r := run("mgr", "task.edit", "e1", `,"task":2,"owner":"boss"`); r["error"] != nil {
		t.Fatalf("%v", r["error"])
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE owner = 'boss' AND reported_at IS NULL AND report IS NULL") != 2 {
		t.Fatal("both moved, both answers cleared")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM events WHERE agent = 'mgr' AND kind IN ('task_handoff', 'edit_task')") != 2 {
		t.Fatal("each change is kept under mgr")
	}
}

// The founder's done closes their own task, and one already answered: the answered rule is only for a first answer to someone else.
func TestTheFoundersDoneClosesTheirOwnTaskAndOneAlreadyAnswered(t *testing.T) {
	sdb, _, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'mine', 'human', 'human', 1, 'human')",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, reported_at, report, report_via) "+
			"VALUES(2, 'theirs', 'human', 'boss', 1, 'boss', 5, 'merged', 'reason')",
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
