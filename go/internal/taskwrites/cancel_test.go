package taskwrites

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestCancelIsTheFoundersDiscardThatStopsTheRequestsOutOnTheTask(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	home := t.TempDir()
	for _, a := range []string{"w", "w2"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(1, 'a', 'w', 'boss', 1, 5), (2, 'b', 'w2', 'human', 1, 5)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body, delivered_at) VALUES(10, 'boss', 'w', 'task', 'queued', 1, 'x', 6), (20, 'human', 'w2', 'task', 'queued', 1, 'x', NULL)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10), (2, 20)",
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('w2', 'IDLE', 1, 2)",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	f := &fakeTeller{}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, home, f)
		return s
	}
	cancel := func(rid string, task int) map[string]any {
		return call(t, mk(""), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.cancel","params":{"task":%d,"request_id":%q}}`, task, rid))[0]
	}
	count := func(q string) (n int) { db.QueryRow(q).Scan(&n); return }

	r := cancel("c1", 1)
	if r["error"] != nil || r["result"].(map[string]any)["said"] != "task T1 discarded by human: cancelled by the founder" {
		t.Fatalf("cancel: %v", r)
	}
	if count("SELECT COUNT(*) FROM requests WHERE id = 10 AND state = 'landed' AND queue_order IS NULL") != 1 ||
		count("SELECT COUNT(*) FROM events WHERE kind = 'cancel' AND payload LIKE '%\"request_id\": 10%'") != 1 {
		t.Fatal("the delivered request is closed and the cancel recorded")
	}
	if len(f.cancelled) != 1 || f.cancelled[0].RID != 10 || !f.cancelled[0].Delivered || !f.cancelled[0].Notify || f.stopped {
		t.Fatalf("the target of a delivered request is stopped by the cancel itself: %+v %v", f.cancelled, f.stopped)
	}

	r = cancel("c2", 2)
	if r["error"] != nil {
		t.Fatalf("the founder's own undelivered request: %v", r)
	}
	if count("SELECT COUNT(*) FROM requests WHERE id = 20 AND state = 'withdrawn'") != 1 || len(f.cancelled) != 0 || !f.stopped || len(f.forget) != 1 || f.forget[0].RID != 20 {
		t.Fatalf("it is withdrawn, its inbox file goes and the holder is typed to stop: %+v %v %+v", f.cancelled, f.stopped, f.forget)
	}
}
