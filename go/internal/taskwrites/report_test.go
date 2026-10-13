package taskwrites

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

// reportEnv is a database with the agents w (who works the tasks), boss (who opens them) and mgr, and a caller of any method as any of them.
func reportEnv(t *testing.T, rows ...string) (*sql.DB, *fakeTeller, func(agent, method, rid, params string) map[string]any) {
	t.Helper()
	sdb, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sdb.Close() })
	home := t.TempDir()
	for _, a := range []string{"w", "boss", "mgr"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	for _, q := range rows {
		if _, err := sdb.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	f := &fakeTeller{}
	return sdb, f, func(agent, method, rid, params string) map[string]any {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = sdb
		Register(s, home, f)
		return call(t, s, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":{"request_id":%q%s}}`, method, rid, params))[0]
	}
}

func said(t *testing.T, r map[string]any) string {
	t.Helper()
	if r["error"] != nil {
		t.Fatalf("refused: %v", r["error"])
	}
	return r["result"].(map[string]any)["said"].(string)
}

func taskState(t *testing.T, sdb *sql.DB, tid int64) string {
	t.Helper()
	tx, err := sdb.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	row, err := getTask(tx, tid)
	if err != nil {
		t.Fatal(err)
	}
	st, err := state(tx, row)
	if err != nil {
		t.Fatal(err)
	}
	return st
}

func TestAnOwnersDoneOnATaskItDoesNotAnswerForIsAReportThatWaitsOpen(t *testing.T) {
	sdb, f, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at) VALUES(1, 'a', 'w', 'boss', 1, 'boss', 1), (2, 'after', 'w', 'boss', 1, 'boss', NULL)",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(2, 1, 1)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(10, 'boss', 'w', 'task', 'queued', 1, 'a')",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)",
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('w', 'IDLE', 1, 1)",
	)
	if got := said(t, run("w", "task.done", "d1", `,"task":1,"evidence":"built and tested"`)); got != "task T1 reported done (built and tested); it closes when boss accepts it" {
		t.Fatalf("said %q", got)
	}
	if st := taskState(t, sdb, 1); st != "reported" {
		t.Fatalf("state %q, want reported", st)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND done_evidence IS NULL AND report = 'built and tested' AND report_via = 'reason' AND sent_at IS NULL") != 1 ||
		count(t, sdb, "SELECT COUNT(*) FROM events WHERE kind = 'mark_task_done'") != 0 {
		t.Fatal("the report is kept and the task is not done")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests WHERE id = 10 AND state = 'withdrawn'") != 1 {
		t.Fatal("the owner's queued delivery closes")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests WHERE target = 'boss' AND state = 'queued' AND notice IS NULL AND body LIKE 'T1 reported done by w: built and tested - accept it (`hl task accept T1`)%'") != 1 {
		t.Fatal("whoever the task answers to has the report in its queue, with how to accept or reject it")
	}
	if len(f.reported) != 1 || f.reported[0] != "T1 w 1" || len(f.done) != 0 {
		t.Fatalf("the teller frees the owner and releases nothing: %v %v", f.reported, f.done)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM task_blocks WHERE task_id = 2 AND blocks_on = 1") != 1 {
		t.Fatal("a reported task still blocks")
	}
	q, _ := sdb.Begin()
	waiting, _ := db.WaitingTasks(q, "w")
	stillAsking, _ := db.CurrentTaskStillAsking(q, "w")
	q.Rollback()
	if len(waiting) != 0 || stillAsking {
		t.Fatalf("the owner is free and the reported task is not sent again: %v %v", waiting, stillAsking)
	}

	if r := run("w", "task.done", "d2", `,"task":1,"evidence":"again"`); errText(r) != "task T1 is already reported done (built and tested) and waits on boss to accept or reject it" {
		t.Fatalf("a second done: %v", r)
	}
	if r := run("boss", "task.done", "d3", `,"task":1,"evidence":"mine"`); errText(r) != "task T1 can only be marked done by its owner (w) or the founder" {
		t.Fatalf("the opener accepts, it does not mark done: %v", r)
	}
}

func TestDoneClosesAtOnceForTheFounderAndForAnOwnerTheTaskAnswersTo(t *testing.T) {
	sdb, f, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, reporter) VALUES(1, 'own', 'w', 'w', 1, NULL), (2, 'f', 'w', 'boss', 1, NULL), "+
			"(3, 'rep', 'w', 'boss', 1, 'w'), (4, 'waits', 'w', 'boss', 1, NULL)",
		"UPDATE tasks SET reported_at = 5, report = 'r', report_via = 'reason' WHERE id = 4",
	)
	said(t, run("w", "task.done", "a", `,"task":1,"evidence":"x"`))
	said(t, run("human", "task.done", "b", `,"task":2,"evidence":"x"`))
	said(t, run("w", "task.done", "c", `,"task":3,"evidence":"x"`))
	said(t, run("human", "task.done", "d", `,"task":4,"evidence":"the founder's"`))
	for tid := int64(1); tid <= 4; tid++ {
		if st := taskState(t, sdb, tid); st != "done" {
			t.Fatalf("T%d is %s", tid, st)
		}
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 4 AND done_evidence = 'the founder''s' AND reported_at IS NULL AND report IS NULL") != 1 {
		t.Fatal("the founder's done settles a waiting report")
	}
	if len(f.done) != 4 || len(f.reported) != 0 {
		t.Fatalf("%v %v", f.done, f.reported)
	}
}

func TestAcceptClosesWithTheReportAndTellsTheOwner(t *testing.T) {
	sdb, f, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, reporter) VALUES(1, 'a', 'w', 'boss', 1, 'mgr'), (2, 'b', 'w', 'boss', 1, NULL)",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(2, 1, 1)",
	)
	if r := run("mgr", "task.accept", "a0", `,"task":1`); errText(r) != "task T1 has no report waiting - nothing to accept" {
		t.Fatalf("nothing to accept: %v", r)
	}
	said(t, run("w", "task.done", "d1", `,"task":1,"evidence":"shipped"`))
	if count(t, sdb, "SELECT COUNT(*) FROM requests WHERE target = 'mgr' AND body LIKE 'T1 reported done by w:%'") != 1 {
		t.Fatal("the report goes to the task's reporter")
	}
	if r := run("w", "task.accept", "a1", `,"task":1`); errText(r) != "task T1's report can only be accepted by mgr - whoever it answers to - or the founder" {
		t.Fatalf("the owner cannot accept its own report: %v", r)
	}
	if got := said(t, run("boss", "task.accept", "a2", `,"task":1`)); got != "task T1 done (shipped), accepted by boss" {
		t.Fatalf("the opener may accept: %q", got)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND done_evidence = 'shipped' AND done_via = 'reason' AND reported_at IS NULL") != 1 ||
		count(t, sdb, "SELECT COUNT(*) FROM events WHERE kind = 'accept_task'") != 1 {
		t.Fatal("the task closes with the report as its evidence")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests WHERE target = 'w' AND body = 'T1 done by w: shipped (accepted by boss)'") != 1 ||
		count(t, sdb, "SELECT COUNT(*) FROM requests WHERE target = 'mgr' AND body = 'T1 done by w: shipped (accepted by boss)'") != 1 {
		t.Fatal("the owner and the reporter are told it was accepted")
	}
	if len(f.done) != 1 || f.done[0] != "T1 boss w 2 true" {
		t.Fatalf("the teller releases what it blocked: %v", f.done)
	}
	if r := run("boss", "task.accept", "a3", `,"task":1`); errText(r) != "task T1 has no report waiting - nothing to accept" {
		t.Fatalf("accepting twice: %v", r)
	}
}

func TestRejectSendsTheTaskBackToItsOwnerWithTheNote(t *testing.T) {
	sdb, f, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(1, 'a', 'w', 'boss', 1, 1)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body, landed_at, delivered_at) VALUES(10, 'boss', 'w', 'task', 'landed', 1, 'a', 1, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)",
	)
	said(t, run("w", "task.done", "d1", `,"task":1,"evidence":"half of it"`))
	if r := run("boss", "task.reject", "r0", `,"task":1`); errText(r) != "note is the one line that says what is missing" {
		t.Fatalf("a reject needs a note: %v", r)
	}
	if r := run("mgr", "task.reject", "r1", `,"task":1,"note":"no"`); errText(r) != "task T1's report can only be rejected by boss - whoever it answers to - or the founder" {
		t.Fatalf("only whoever it answers to rejects: %v", r)
	}
	if got := said(t, run("boss", "task.reject", "r2", `,"task":1,"note":"the tests are missing"`)); got != "task T1 sent back to w: the tests are missing - delivered T1" {
		t.Fatalf("said %q", got)
	}
	if st := taskState(t, sdb, 1); st != "queued" {
		t.Fatalf("state %q, want queued", st)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND reported_at IS NULL AND report IS NULL AND done_evidence IS NULL AND queued_by = 'boss'") != 1 ||
		count(t, sdb, "SELECT COUNT(*) FROM events WHERE kind = 'reject_task' AND json_extract(payload, '$.note') = 'the tests are missing'") != 1 {
		t.Fatal("the report is dropped and the task is queued again")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests WHERE target = 'w' AND state = 'queued' AND body = 'T1 sent back by boss: the tests are missing'") != 1 ||
		len(f.rejected) != 1 || f.rejected[0] != "T1 boss 1" {
		t.Fatalf("the owner is told the note: %v", f.rejected)
	}
	q, _ := sdb.Begin()
	waiting, _ := db.WaitingTasks(q, "w")
	q.Rollback()
	if len(waiting) != 1 || waiting[0].Task.ID != 1 {
		t.Fatalf("the rejected task is due to its owner again, though it was delivered before: %v", waiting)
	}
	if got := said(t, run("w", "task.done", "d2", `,"task":1,"evidence":"all of it"`)); got != "task T1 reported done (all of it); it closes when boss accepts it" {
		t.Fatalf("a second report: %q", got)
	}
}
