package taskwrites

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestDiscardIsTheAskersAndTellsTheOwnerAndFreesWhatWaitedOnIt(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	home := t.TempDir()
	for _, a := range []string{"worker", "manager", "other"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'worker', 'manager', 1), (2, 'u', 'other', 'other', 1), (3, 'child', 'worker', 'worker', 1)",
		"UPDATE tasks SET parent_task = 1 WHERE id = 3",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(2, 1, 1)",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, home)
		return s
	}
	line := func(id int, task int, reason, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.discard","params":{"task":%d,"reason":%q,"request_id":%q}}`, id, task, reason, rid)
	}
	if r := call(t, mk("other"), line(2, 1, "x", "o1")); r[0]["error"] == nil {
		t.Fatalf("only the asker or the founder discards: %v", r[0])
	}
	if r := call(t, mk("manager"), line(2, 1, "x", "m1")); r[0]["error"] == nil {
		t.Fatalf("an open child refuses: %v", r[0])
	}
	db.Exec("UPDATE tasks SET done_evidence = 'x', done_at = 2 WHERE id = 3")
	r := call(t, mk("manager"), line(2, 1, "not needed", "m2"), line(3, 1, "again", "m3"))
	if r[0]["result"].(map[string]any)["said"] != "task T1 discarded by manager: not needed" || r[1]["error"] == nil {
		t.Fatalf("%v %v", r[0], r[1])
	}
	var n int
	db.QueryRow("SELECT COUNT(*) FROM tasks WHERE id = 1 AND discarded_by = 'manager' AND discard_reason = 'not needed'").Scan(&n)
	if n != 1 {
		t.Fatal("task 1 is not recorded as discarded")
	}
	var body, target string
	var notice int
	db.QueryRow("SELECT body, target, COALESCE(notice, 0) FROM requests WHERE requester = 'task-board' AND body LIKE 'T1 discarded%'").Scan(&body, &target, &notice)
	if body != "T1 discarded by manager: not needed" || target != "worker" || notice != 1 {
		t.Fatalf("the owner is told: %q %q %d", body, target, notice)
	}
	db.QueryRow("SELECT COUNT(*) FROM requests WHERE requester = 'task-board' AND target = 'other' AND body LIKE 'task T2 is unblocked - T1%'").Scan(&n)
	var blocks int
	db.QueryRow("SELECT COUNT(*) FROM task_blocks").Scan(&blocks)
	if n != 1 || blocks != 0 {
		t.Fatalf("T2 is told it is unblocked once and the block is gone: %d %d", n, blocks)
	}
	w := call(t, mk("worker"), `{"jsonrpc":"2.0","id":2,"method":"task.withdraw","params":{"task":2,"request_id":"w1"}}`)
	if w[0]["error"] == nil {
		t.Fatalf("only a task's opener or the founder withdraws it: %v", w[0])
	}
}

func discardEnv(t *testing.T) (*sql.DB, func(agent string) *rpc.Server) {
	t.Helper()
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	home := t.TempDir()
	for _, a := range []string{"gp", "worker", "other", "manager"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	return db, func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, home)
		return s
	}
}

func exec(t *testing.T, db *sql.DB, qs ...string) {
	t.Helper()
	for _, q := range qs {
		if _, err := db.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
}

func count(t *testing.T, db *sql.DB, q string) int {
	t.Helper()
	var n int
	if err := db.QueryRow(q).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func discardLine(id, task int, reason, rid string) string {
	return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.discard","params":{"task":%d,"reason":%q,"request_id":%q}}`, id, task, reason, rid)
}

func TestTheFoundersDiscardMovesChildrenUpAndDropsItsOwnWaits(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(10, 'gp', 'gp', 'manager', 1), (11, 'mid', 'worker', 'manager', 1), (12, 'kid', 'other', 'other', 1), (13, 'dep', 'worker', 'worker', 1)",
		"UPDATE tasks SET parent_task = 10 WHERE id = 11",
		"UPDATE tasks SET parent_task = 11 WHERE id = 12",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(11, 12, 1), (11, 13, 1)")
	if r := call(t, mk("manager"), discardLine(2, 11, "x", "m1")); r[0]["error"] == nil {
		t.Fatalf("an agent cannot discard a task with an open child: %v", r[0])
	}
	r := call(t, mk(""), discardLine(2, 11, "gone", "f1"))
	if r[0]["result"] == nil {
		t.Fatalf("the founder discards: %v", r[0])
	}
	if count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 12 AND parent_task = 10") != 1 ||
		count(t, db, "SELECT COUNT(*) FROM task_blocks WHERE task_id = 10 AND blocks_on = 12") != 1 ||
		count(t, db, "SELECT COUNT(*) FROM task_blocks WHERE task_id = 11") != 0 {
		t.Fatal("the child moves up with the wait, and the task's own waits are dropped")
	}
	if count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 11 AND discarded_by = 'human'") != 1 ||
		count(t, db, "SELECT COUNT(*) FROM requests WHERE requester = 'task-board' AND target = 'worker' AND body = 'T11 discarded by the founder: gone'") != 1 { // local-ref-ok
		t.Fatal("recorded as the founder's, and the owner is told")
	}
}

func TestADiscardedChildIsToldToItsParentsOwnerOnce(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(20, 'parent', 'worker', 'manager', 1), (21, 'kid', 'other', 'other', 1)",
		"UPDATE tasks SET parent_task = 20 WHERE id = 21",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(20, 21, 1)")
	if r := call(t, mk("other"), discardLine(2, 21, "not needed", "o1")); r[0]["result"] == nil {
		t.Fatalf("%v", r[0])
	}
	if count(t, db, "SELECT COUNT(*) FROM requests WHERE target = 'worker' AND body = 'T21 discarded by other: not needed'") != 1 || // local-ref-ok
		count(t, db, "SELECT COUNT(*) FROM requests WHERE target = 'worker' AND body LIKE 'task T20 is unblocked - T21%'") != 1 || // local-ref-ok
		count(t, db, "SELECT COUNT(*) FROM task_requests WHERE task_id = 20") != 2 {
		t.Fatal("the parent's owner hears the result and that the task is unblocked")
	}
	tx, err := db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	parent, _ := getTask(tx, 20)
	if n, err := childResultNotice(tx, parent, "other", 21); err != nil || n != nil {
		t.Fatalf("the result is told once: %v %v", n, err)
	}
}

func TestAnOpenRequestRefusesTheDiscardAndWithdrawGoesThrough(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(30, 'a', 'worker', 'manager', 1), (31, 'b', 'worker', 'manager', 1)",
		"INSERT INTO requests(id, requester, target, body, state, kind, queue_order, ts) VALUES(40, 'manager', 'worker', 'do a', 'queued', 'task', 40, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(30, 40)")
	r := call(t, mk("manager"), discardLine(2, 30, "x", "m1"))
	if r[0]["error"] == nil || count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 30 AND discarded_by IS NULL") != 1 {
		t.Fatalf("a request still out refuses: %v", r[0])
	}
	w := call(t, mk("manager"), `{"jsonrpc":"2.0","id":2,"method":"task.withdraw","params":{"task":31,"request_id":"w1"}}`)
	if w[0]["result"] == nil || count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 31 AND discard_reason = 'withdrawn by whoever opened it'") != 1 {
		t.Fatalf("the opener withdraws: %v", w[0])
	}
}

func TestClosingQueuedDeliveriesQuietly(t *testing.T) {
	db, _ := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(50, 'a', 'worker', 'manager', 1, 1)",
		"INSERT INTO requests(id, requester, target, body, state, kind, queue_order, ts, landed_at, chain_id) VALUES(60, 'manager', 'worker', 'seen', 'queued', 'task', 60, 1, 5, '60'), (61, 'manager', 'worker', 'unseen', 'queued', 'task', 61, 1, NULL, '61')",
		"INSERT INTO task_requests(task_id, request_id) VALUES(50, 60), (50, 61)")
	tx, err := db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	reqs, _ := taskRequests(tx, 50)
	for _, r := range reqs {
		if err := closeQueuedQuietly(tx, r); err != nil {
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	if count(t, db, "SELECT COUNT(*) FROM requests WHERE id = 60 AND state = 'landed' AND queue_order IS NULL") != 1 ||
		count(t, db, "SELECT COUNT(*) FROM requests WHERE id = 61 AND state = 'withdrawn' AND queue_order IS NULL") != 1 {
		t.Fatal("a landed one closes as read, an unseen one is withdrawn")
	}
	if count(t, db, "SELECT COUNT(*) FROM requests WHERE settles = 60 AND body = '' AND requester = 'worker' AND target = 'manager' AND delivered_at IS NOT NULL AND state = 'landed'") != 1 ||
		count(t, db, "SELECT COUNT(*) FROM requests WHERE settles = 61") != 0 {
		t.Fatal("only the landed one gets its empty answer, already told")
	}
	if count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 50 AND sent_at IS NULL") != 1 {
		t.Fatal("the task no longer reads as sent")
	}
}
