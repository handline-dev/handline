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
		Register(s, home)
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
}
