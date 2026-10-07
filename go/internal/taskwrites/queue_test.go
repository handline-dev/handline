package taskwrites

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestQueueAndDraftAreTheFoundersOrTheManagersAndAllOrNone(t *testing.T) {
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
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte("agents:\n  manager: {}\n  worker: {}\n  other: {}\nedges:\n  - {from: manager, to: worker, relation: supervisor, response: async}\n"), 0o644)
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
		Register(s, home, nil)
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
	if r := call(t, mk("other"), line(2, "task.queue", "o1", 1)); r[0]["error"] == nil || state(1) {
		t.Fatalf("only the founder or the owner's manager queues: %v", r[0])
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
		if msg := errText(r[0]); msg != "task T4 is doing - only a queued task can be made draft" && msg != "task T4 is doing - only a draft task can be made queued" {
			t.Fatalf("%s of a task with a request out: %v", m, r[0])
		}
	}
	db.Exec("UPDATE agent_state SET current_task = 1 WHERE agent = 'worker'")                                                // it holds another open task: this one is asked but held back
	db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(6, 'f', 'worker', 'manager', 1, 5)") // never approved, asked, held back
	if msg := errText(call(t, mk("manager"), line(2, "task.queue", "h1", 6))[0]); msg != "task T6 is already queued" {
		t.Fatalf("asked but held back reads queued: %q", msg)
	}
	db.Exec("UPDATE tasks SET discarded_by = 'manager', discarded_at = 3 WHERE id = 2")
	if msg := errText(call(t, mk("manager"), line(2, "task.draft", "x1", 2))[0]); msg != "task T2 is discarded - only a queued task can be made draft" {
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
