package taskwrites

import (
	"fmt"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestMergeFoldsTasksIntoOneAndRefusesWhatWouldLoopOrIsNotTheirs(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'keep', 'w', 'boss', 1), (2, 'two', 'w', 'boss', 1), (3, 'three', 'w', 'boss', 1), " +
			"(4, 'dep', 'w', 'boss', 1), (5, 'other', 'w', 'else', 1), (6, 'child', 'w', 'boss', 1), (7, 'waiting', 'w', 'boss', 1), (8, 'blocker', 'w', 'boss', 1), (9, 'finished', 'w', 'boss', 1), (10, 'gone', 'w', 'boss', 1)",
		"UPDATE tasks SET done_evidence = 'x', done_at = 2 WHERE id = 9",
		"UPDATE tasks SET discarded_by = 'boss', discarded_at = 2 WHERE id = 10",
		"UPDATE tasks SET sent_at = 5 WHERE id = 2", // the request out on T2 makes it read as sent
		"UPDATE tasks SET parent_task = 2 WHERE id = 6",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(10, 'boss', 'w', 'task', 'queued', 1, 'x')",
		"INSERT INTO task_requests(task_id, request_id) VALUES(2, 10)",
		"INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(1, 2, 'boss', 'note', 1)",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(4, 2, 1), (1, 2, 1), (2, 6, 1)", // 4 waits on 2; keep waits on 2; 2 waits on its child
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(7, 8, 1)",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, t.TempDir(), nil)
		return s
	}
	merge := func(agent, rid string, into int, tasks ...int) map[string]any {
		return call(t, mk(agent), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.merge","params":{"into":%d,"tasks":%v,"request_id":%q}}`, into, jsonList(tasks), rid))[0]
	}
	refused := func(why string, r map[string]any, want string) {
		t.Helper()
		if got := errText(r); got != want {
			t.Fatalf("%s: %q, want %q", why, got, want)
		}
	}

	refused("not the opener", merge("else", "a1", 1, 2), "task T1 can only be edited by whoever opened it (boss) or the founder")
	refused("itself", merge("boss", "a2", 1, 1), "task T1 cannot merge into itself")
	refused("twice", merge("boss", "a3", 1, 2, 2), "a task is named twice")
	refused("unknown", merge("boss", "a4", 1, 99), fmt.Sprintf("no task T%d", 99))
	refused("not theirs", merge("boss", "a5", 1, 5), "task T5 can only be edited by whoever opened it (else) or the founder")
	refused("a real blocker", merge("boss", "a6", 1, 7), "task T7 is waiting on T8 - it cannot be merged until they are done")
	refused("under it", merge("boss", "a7", 6, 2), "task T6 is under T2 - folding T2 into it would loop them")
	db.Exec("INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(1, 4, 1)") // keep waits on 4, 4 waits on 2: folding 2 into 1 would have 4 wait on 1, which waits on 4
	refused("a wait cycle", merge("boss", "a8", 1, 2), "T4 waits on T2; waiting on T1 instead would be a cycle")
	db.Exec("DELETE FROM task_blocks WHERE task_id = 1 AND blocks_on = 4")
	refused("an unknown kept task", merge("boss", "b1", 98, 2), fmt.Sprintf("no task T%d", 98))
	refused("a discarded kept task", merge("boss", "b2", 10, 2), fmt.Sprintf("task T%d is discarded - nothing can merge into it", 10))
	refused("a done folded task", merge("boss", "b3", 1, 9), "task T9 is done - it is not merged")
	refused("a discarded folded task", merge("boss", "b4", 1, 10), fmt.Sprintf("task T%d is already discarded", 10))
	refused("no tasks", call(t, mk("boss"), `{"jsonrpc":"2.0","id":2,"method":"task.merge","params":{"into":1,"tasks":[],"request_id":"b5"}}`)[0], "tasks is a list of task numbers")
	var n int
	db.QueryRow("SELECT COUNT(*) FROM tasks WHERE discarded_by IS NOT NULL").Scan(&n)
	if n != 1 { // only the one the test seeded
		t.Fatalf("refusals change nothing: %d discarded", n)
	}

	r := merge("boss", "ok", 1, 2, 3)
	if r["error"] != nil || r["result"].(map[string]any)["said"] != "T2, T3 merged into T1 by boss" {
		t.Fatalf("merge: %v", r)
	}
	var title, by, reason string
	var into int
	db.QueryRow("SELECT title FROM tasks WHERE id = 1").Scan(&title)
	if title != "keep (+ T2: two) (+ T3: three)" {
		t.Fatalf("the titles are added: %q", title)
	}
	db.QueryRow("SELECT discarded_by, discard_reason, merged_into FROM tasks WHERE id = 2").Scan(&by, &reason, &into)
	if by != "boss" || reason != "merged into T1" || into != 1 {
		t.Fatalf("T2 closes as merged: %q %q %d", by, reason, into)
	}
	count := func(q string) (n int) { db.QueryRow(q).Scan(&n); return }
	if count("SELECT COUNT(*) FROM task_requests WHERE task_id = 1 AND request_id = 10") != 1 || count("SELECT COUNT(*) FROM task_requests WHERE task_id = 2") != 0 {
		t.Fatal("the request moves")
	}
	if count("SELECT COUNT(*) FROM task_memos WHERE task_id = 1") != 1 || count("SELECT parent_task FROM tasks WHERE id = 6") != 1 {
		t.Fatal("the memo and the sub-task move")
	}
	if count("SELECT COUNT(*) FROM task_blocks WHERE task_id = 4 AND blocks_on = 1") != 1 || count("SELECT COUNT(*) FROM task_blocks WHERE blocks_on = 2 OR task_id = 2 OR task_id = 1 AND blocks_on = 1") != 0 {
		t.Fatal("the wait on T2 becomes a wait on T1, and keep's own wait on T2 is dropped")
	}
	if count("SELECT COUNT(*) FROM task_blocks WHERE task_id = 1 AND blocks_on = 6") != 1 {
		t.Fatal("the wait T2 held on its child moves to the kept task")
	}
	if count("SELECT COUNT(*) FROM tasks WHERE id = 1 AND sent_at IS NOT NULL") != 1 || count("SELECT COUNT(*) FROM tasks WHERE id = 2 AND sent_at IS NULL") != 1 {
		t.Fatal("the request out on T2 now makes the kept task read as sent, and T2 not")
	}
	if count("SELECT COUNT(*) FROM events WHERE kind = 'merge_task' AND payload LIKE '%dropped_wait%'") != 1 {
		t.Fatal("the dropped wait is on the event")
	}
	refused("again", merge("boss", "again", 1, 2), "task T2 is already discarded")
	db.Exec("UPDATE tasks SET done_evidence = 'x', done_at = 2 WHERE id = 1")
	refused("into a done task", merge("boss", "a9", 1, 4), "task T1 is done - nothing can merge into it")
}

func jsonList(ns []int) string {
	s := "["
	for i, n := range ns {
		if i > 0 {
			s += ","
		}
		s += fmt.Sprint(n)
	}
	return s + "]"
}

func TestMergeRefreshesSentFromTheRequestsThatMoved(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'keep', 'w', 'boss', 1), (2, 'queued', 'w', 'boss', 1), (3, 'answered', 'w', 'boss', 1), (4, 'keep2', 'w', 'boss', 1)",
		"UPDATE tasks SET sent_at = 5 WHERE id IN (1, 2, 3, 4)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(10, 'boss', 'w', 'task', 'queued', 1, 'x'), (11, 'boss', 'w', 'task', 'done', 1, 'x')",
		"INSERT INTO task_requests(task_id, request_id) VALUES(2, 10), (3, 11)",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	s := rpc.NewServer(auth{"boss"}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = db
	Register(s, t.TempDir(), nil)
	for i, into := range [][2]int{{1, 2}, {4, 3}} {
		r := call(t, s, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.merge","params":{"into":%d,"tasks":[%d],"request_id":"m%d"}}`, into[0], into[1], i))[0]
		if r["error"] != nil {
			t.Fatalf("merge into T%d: %v", into[0], r)
		}
	}
	sent := func(id int) (n int) {
		db.QueryRow("SELECT sent_at IS NOT NULL FROM tasks WHERE id = ?", id).Scan(&n)
		return
	}
	if sent(1) != 1 {
		t.Fatal("a still-queued request moved to the kept task: it reads as sent")
	}
	if sent(4) != 0 {
		t.Fatal("an answered request moved to the kept task: it no longer reads as sent")
	}
}
