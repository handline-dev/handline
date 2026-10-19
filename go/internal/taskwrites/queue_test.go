package taskwrites

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestQueueAndDraftAreAllOrNone(t *testing.T) {
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
	os.MkdirAll(filepath.Join(home, "agents"), 0o755)
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte("agents:\n  manager: {}\n  worker: {}\n  other: {}\nedges:\n  - {from: manager, to: worker}\n"), 0o644)
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'worker', 'manager', 1), (2, 'b', 'worker', 'manager', 1), (3, 'c', 'worker', 'manager', 1)",
		"UPDATE tasks SET done_evidence = 'x', done_at = 2 WHERE id = 3",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, home, nil, nil)
		return s
	}
	line := func(id int, method, rid string, tasks ...int) string {
		list, _ := json.Marshal(tasks)
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":%q,"params":{"tasks":%s,"request_id":%q}}`, id, method, list, rid)
	}
	state := func(id int) (queued bool) {
		var q *string
		db.QueryRow("SELECT queued_by FROM tasks WHERE id = ?", id).Scan(&q)
		return q != nil
	}
	if r := call(t, mk("manager"), line(2, "task.queue", "m0", 1, 3)); r[0]["error"] == nil || state(1) {
		t.Fatalf("one task that cannot be queued stops all of them: %v", r[0])
	}
	r := call(t, mk("manager"), line(2, "task.queue", "m1", 1, 2), line(3, "task.queue", "m2", 1))
	if r[0]["result"] == nil {
		t.Fatalf("queue refused: %v", r[0])
	}
	if r[0]["result"].(map[string]any)["said"] != "task T1 is queued (manager)\ntask T2 is queued (manager)" || !state(1) || !state(2) || r[1]["error"] == nil {
		t.Fatalf("%v %v", r[0], r[1])
	}
	var kind string
	db.QueryRow("SELECT kind FROM events WHERE agent = 'manager' AND kind != 'rpc_write' ORDER BY id DESC LIMIT 1").Scan(&kind)
	if kind != "queue_task" {
		t.Fatalf("the write leaves an event: %q", kind)
	}
	r = call(t, mk("manager"), line(2, "task.draft", "m3", 1), line(3, "task.draft", "m4", 1))
	if r[0]["error"] != nil || state(1) || r[1]["error"] == nil {
		t.Fatalf("a queued task goes back to draft once: %v %v", r[0], r[1])
	}
	f := call(t, mk(""), line(2, "task.queue", "f1", 1))
	if f[0]["error"] != nil || !state(1) {
		t.Fatalf("the founder queues: %v", f[0])
	}
	bad := call(t, mk(""), `{"jsonrpc":"2.0","id":2,"method":"task.queue","params":{"tasks":"1","request_id":"b1"}}`)
	if bad[0]["error"] == nil {
		t.Fatalf("tasks is a list of numbers: %v", bad[0])
	}

	// a task with work out on it is not taken back or queued again; held back behind its owner's current task it reads queued
	db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at) VALUES(4, 'd', 'worker', 'manager', 1, 'manager', 5)")
	if _, err := db.Exec("INSERT INTO agent_state(agent, state, updated, current_task) VALUES('worker', 'RUNNING', 1, 4)"); err != nil {
		t.Fatal(err)
	}
	for _, m := range []string{"task.draft", "task.queue"} {
		r := call(t, mk("manager"), line(2, m, "d-"+m, 4))
		if msg := errText(r[0]); msg != "task T4 is still doing" && msg != "task T4 is doing - only a draft task can be made queued" {
			t.Fatalf("%s of a task with a request out: %v", m, r[0])
		}
	}
	db.Exec("UPDATE agent_state SET current_task = 1 WHERE agent = 'worker'")                                                // it holds another open task: this one is asked but held back
	db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(6, 'f', 'worker', 'manager', 1, 5)") // never approved, asked, held back
	if msg := errText(call(t, mk("manager"), line(2, "task.queue", "h1", 6))[0]); msg != "task T6 is already queued" {
		t.Fatalf("asked but held back reads queued: %q", msg)
	}
	db.Exec("UPDATE tasks SET discarded_by = 'manager', discarded_at = 3 WHERE id = 2")
	if msg := errText(call(t, mk("manager"), line(2, "task.draft", "x1", 2))[0]); msg != "task T2 is discarded - only a queued or doing task can be made draft" {
		t.Fatalf("a discarded task: %q", msg)
	}
	nf := call(t, mk("manager"), line(2, "task.queue", "n1", 99))[0]["error"].(map[string]any)
	if nf["data"].(map[string]any)["what"] != "task" {
		t.Fatalf("an unknown task is not_found: %v", nf)
	}
	db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(5, 'e', 'worker', 'manager', 1)")
	dup := call(t, mk("manager"), line(2, "task.queue", "u1", 5, 5))[0]["result"].(map[string]any)
	if dup["said"] != "task T5 is queued (manager)" {
		t.Fatalf("a repeated id queues once: %v", dup)
	}
}

func errText(r map[string]any) string {
	e, ok := r["error"].(map[string]any)
	if !ok {
		return ""
	}
	s, _ := e["message"].(string)
	return s
}

// A draft is worked by nobody: the delivery still waiting is withdrawn, one that has landed is ended, and the task then reads draft.
func TestDraftWithdrawsTheDeliveryNotYetLandedAndEndsOneThatHas(t *testing.T) {
	sdb, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer sdb.Close()
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at) VALUES(1, 'sent', 'w', 'boss', 1, NULL, 5), " +
			"(2, 'landed', 'w', 'boss', 1, 'boss', 5), (3, 'approved', 'w', 'boss', 1, 'boss', NULL), (4, 'parent', 'w', 'boss', 1, NULL, NULL), " +
			"(5, 'child', 'w', 'boss', 1, NULL, 5), (7, 'held', 'w', 'boss', 1, 'boss', 5)",
		"UPDATE tasks SET parent_task = 4 WHERE id = 5",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body, delivered_at) VALUES(10, 'boss', 'w', 'task', 'queued', 1, 'x', NULL), " +
			"(11, 'boss', 'w', 'task', 'landed', 1, 'x', 6), (12, 'boss', 'w', 'task', 'queued', 1, 'x', NULL)",
		"UPDATE requests SET queue_order = 1 WHERE id = 11",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10), (2, 11), (5, 12)",
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('w', 'RUNNING', 1, 7)", // w works T7: the others are held back
	} {
		if _, err := sdb.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	f := &fakeTeller{}
	s := rpc.NewServer(auth{"boss"}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = sdb
	Register(s, t.TempDir(), f, nil)
	draft := func(rid string, tid int) map[string]any {
		return call(t, s, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.draft","params":{"tasks":[%d],"request_id":%q}}`, tid, rid))[0]
	}
	stateOf := func(tid int64) string {
		tx, _ := sdb.Begin()
		defer tx.Rollback()
		tk, _ := getTask(tx, tid)
		st, err := state(tx, tk)
		if err != nil {
			t.Fatal(err)
		}
		return st
	}
	var reqState string

	r := draft("d1", 1)
	if r["error"] != nil || r["result"].(map[string]any)["said"] != "delivered T1 - 1 stopped" || stateOf(1) != "draft" {
		t.Fatalf("a delivery not yet landed is withdrawn: %v, T1 %s", r, stateOf(1))
	}
	sdb.QueryRow("SELECT state FROM requests WHERE id = 10").Scan(&reqState)
	if reqState != "withdrawn" || len(f.released) != 1 || f.released[0].RID != 10 || f.released[0].Held {
		t.Fatalf("#10 %s, told %+v", reqState, f.released)
	}

	f.released = nil
	if r := draft("d2", 2); r["error"] != nil || stateOf(2) != "draft" {
		t.Fatalf("a landed delivery held back behind w's current task: %v, T2 %s", r, stateOf(2))
	}
	var order *float64
	sdb.QueryRow("SELECT state, queue_order FROM requests WHERE id = 11").Scan(&reqState, &order)
	if reqState != "landed" || order != nil || f.released != nil {
		t.Fatalf("ended, and w (working T7) not told to stop: #11 %s %v, told %+v", reqState, order, f.released)
	}

	if r := draft("d3", 3); r["error"] != nil || stateOf(3) != "draft" || f.released != nil {
		t.Fatalf("a queued task with no request out: %v, T3 %s", r, stateOf(3))
	}

	if e := errText(draft("d4", 4)); e != "task T4 is still queued: its child T5 is out - make that draft first" || stateOf(4) != "queued" {
		t.Fatalf("asked through a child: %q, T4 %s", e, stateOf(4))
	}
}

// A task that landed with its holder working it is parked: the holder is stopped and moved on, its timeout paused, and what it left in its
// worktree is kept in a memo.
func TestDraftParksALandedTaskItsHolderIsWorking(t *testing.T) {
	sdb, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer sdb.Close()
	wt := t.TempDir()
	git := func(args ...string) {
		t.Helper()
		if r := gitrun.Run(wt, args...); r.RC != 0 {
			t.Fatalf("git %v: %s", args, r.Stderr)
		}
	}
	git("init", "-q", "-b", "main")
	git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "base")
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at, timeout_at) VALUES(1, 'idle', 'w', 'boss', 1, 'boss', 5, 99), " +
			"(2, 'with commits', 'v', 'boss', 1, 'boss', 5, 99)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body, delivered_at, landed_at, queue_order) VALUES" +
			"(11, 'boss', 'w', 'task', 'landed', 1, 'x', 6, 6, 1), (12, 'boss', 'v', 'task', 'landed', 1, 'x', 6, 6, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 11), (2, 12)",
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('w', 'RUNNING', 1, 1)",
	} {
		if _, err := sdb.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	if _, err := sdb.Exec("INSERT INTO agent_state(agent, state, updated, current_task, worktree) VALUES('v', 'RUNNING', 1, 2, ?)", "repo::"+wt); err != nil {
		t.Fatal(err)
	}
	home := t.TempDir() // w and v are agents, so a notice to either is written
	for _, a := range []string{"w", "v"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	f := &fakeTeller{}
	s := rpc.NewServer(auth{"boss"}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = sdb
	Register(s, home, f, nil)
	draft := func(rid string, tid int) map[string]any {
		return call(t, s, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.draft","params":{"tasks":[%d],"request_id":%q}}`, tid, rid))[0]
	}
	read := func(tid int64) (st string, timeout *int64, memos []string) {
		tx, _ := sdb.Begin()
		defer tx.Rollback()
		tk, _ := getTask(tx, tid)
		if st, err = state(tx, tk); err != nil {
			t.Fatal(err)
		}
		tx.QueryRow("SELECT timeout_at FROM tasks WHERE id = ?", tid).Scan(&timeout)
		rows, _ := tx.Query("SELECT body FROM task_memos WHERE task_id = ?", tid)
		for rows.Next() {
			var b string
			rows.Scan(&b)
			memos = append(memos, b)
		}
		rows.Close()
		return
	}

	r := draft("p1", 1)
	if st, timeout, memos := read(1); r["error"] != nil || st != "draft" || timeout != nil || memos != nil {
		t.Fatalf("a landed task with an idle holder: %v, T1 %s timeout %v memos %q", r, st, timeout, memos)
	}
	if said := r["result"].(map[string]any)["said"]; said != "delivered T1 - 1 stopped" ||
		len(f.released) != 1 || f.released[0] != (Retired{RID: 11, Requester: "boss", Target: "w", Held: true, Park: true}) {
		t.Fatalf("w is parked: %q %+v", said, f.released)
	}
	var notices int
	sdb.QueryRow("SELECT COUNT(*) FROM requests WHERE requester = ? AND target = 'w'", noticeFrom).Scan(&notices)
	if notices != 0 {
		t.Fatalf("the parked holder is told by its stop, not by the draft notice too: %d notices", notices)
	}

	os.WriteFile(filepath.Join(wt, "a.go"), []byte("package a\n"), 0o644)
	git("checkout", "-q", "-b", "work")
	git("add", "a.go")
	git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "half done")
	head := strings.TrimSpace(gitrun.Run(wt, "rev-parse", "--short", "HEAD").Stdout)
	f.released = nil
	r = draft("p2", 2)
	st, _, memos := read(2)
	want := fmt.Sprintf("parked while v worked it: branch work @ %s in %s (1 commit(s) since it landed) - resume from there", head, wt)
	if r["error"] != nil || st != "draft" || len(memos) != 1 || memos[0] != want || len(f.released) != 1 || !f.released[0].Park {
		t.Fatalf("a landed task with commits: %v, T2 %s memos %q, told %+v", r, st, memos, f.released)
	}
	if said := r["result"].(map[string]any)["said"]; said != "delivered T2, memo 1 keeps where v stopped - 1 stopped" {
		t.Fatalf("the reply names the memo: %q", said)
	}
}
