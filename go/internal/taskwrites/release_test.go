package taskwrites

import (
	"fmt"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestReleaseTakesADoingTaskBackAndRetiresTheRequestsOutOnIt(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at, timeout_s, timeout_at) VALUES(1, 'q', 'w', 'boss', 1, 'boss', 5, 60, 99), " +
			"(2, 'held', 'w2', 'boss', 1, NULL, 5, NULL, NULL), (3, 'parent', 'w3', 'boss', 1, 'boss', NULL, NULL, NULL), (4, 'child', 'w3', 'boss', 1, 'boss', 5, NULL, NULL), (5, 'idle', 'w4', 'boss', 1, 'boss', NULL, NULL, NULL)",
		"UPDATE tasks SET parent_task = 3 WHERE id = 4",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body, delivered_at) VALUES(10, 'boss', 'w', 'task', 'queued', 1, 'x', 6), " +
			"(11, 'boss', 'w2', 'task', 'landed', 1, 'x', 6), (12, 'boss', 'w3', 'task', 'queued', 1, 'x', 6)",
		"UPDATE requests SET queue_order = 1 WHERE id = 11",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10), (2, 11), (4, 12)",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	f := &fakeTeller{}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, t.TempDir(), f)
		return s
	}
	release := func(agent, rid string, task int, extra string) map[string]any {
		return call(t, mk(agent), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.release","params":{"task":%d,"request_id":%q%s}}`, task, rid, extra))[0]
	}
	refused := func(why string, r map[string]any, want string) {
		t.Helper()
		if got := errText(r); got != want {
			t.Fatalf("%s: %q, want %q", why, got, want)
		}
	}
	refused("not the opener", release("else", "a1", 1, ""), "task T1 can only be edited by whoever opened it (boss) or the founder")
	refused("unknown", release("boss", "a2", 99, ""), "no task T99")
	refused("not doing", release("boss", "a3", 5, ""), "task T5 is queued, not doing - nothing to release")
	refused("through a child", release("boss", "a4", 3, ""), "task T3 is doing through its child T4 - release it there")
	if e := errText(release("boss", "a5", 1, `,"to":"draft"`)); e != "to is not served yet" {
		t.Fatalf("a destination: %q", e)
	}

	r := release("boss", "ok", 1, "")
	if r["error"] != nil || r["result"].(map[string]any)["said"] != "task T1 released by boss: 1 stopped, back to queued, its timeout paused" {
		t.Fatalf("release: %v", r)
	}
	var state string
	db.QueryRow("SELECT state FROM requests WHERE id = 10").Scan(&state)
	count := func(q string) (n int) { db.QueryRow(q).Scan(&n); return }
	if state != "landed" || count("SELECT COUNT(*) FROM tasks WHERE id = 1 AND sent_at IS NULL AND timeout_at IS NULL") != 1 {
		t.Fatalf("the delivered request closes as landed and the task is unsent with its clock paused: %q", state)
	}
	if count("SELECT COUNT(*) FROM events WHERE kind = 'release_task' AND payload = '{\"task_id\": 1, \"retired\": [10]}'") != 1 ||
		count("SELECT COUNT(*) FROM events WHERE kind = 'closed_under'") != 1 {
		var kinds, payloads string
		db.QueryRow("SELECT group_concat(kind), group_concat(payload, ' | ') FROM events").Scan(&kinds, &payloads)
		t.Fatalf("the release and the close are on the event log: %s %s", kinds, payloads)
	}
	if len(f.released) != 1 || f.released[0].RID != 10 || f.released[0].Held || !f.released[0].Delivered || !f.released[0].Notify {
		t.Fatalf("the teller is handed the closed request: %+v", f.released)
	}

	r = release("boss", "held", 2, "")
	if r["error"] != nil || r["result"].(map[string]any)["said"] != "task T2 released by boss: 1 stopped, back to draft, its timeout paused" {
		t.Fatalf("a held request: %v", r)
	}
	if count("SELECT COUNT(*) FROM requests WHERE id = 11 AND state = 'landed' AND queue_order IS NULL") != 1 || !f.released[0].Held {
		t.Fatal("a held request leaves the owner's queue and the owner is to be told to stop")
	}
	refused("again", release("boss", "again", 1, ""), "task T1 is queued, not doing - nothing to release")
}
