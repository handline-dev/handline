package taskwrites

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestBeginOpensATaskOnTheFoundersMessageAndStartsIt(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec("INSERT INTO requests(id, requester, target, body, state, kind, ts) VALUES(1, 'human', 'worker', 'do it', 'queued', 'say', 1), " +
		"(2, 'human', 'worker', 'and this', 'queued', 'say', 1), (3, 'manager', 'worker', 'not the founder', 'queued', 'task', 1)"); err != nil {
		t.Fatal(err)
	}
	s := rpc.NewServer(auth{"worker"}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = db
	Register(s, t.TempDir(), nil)
	line := func(id int, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.begin","params":{%s"request_id":%q}}`, id, params, rid)
	}
	r := call(t, s,
		line(2, `"title":"one","message":1,"why":"asked","priority":"p1","expects":"sha",`, "b1"),
		line(3, `"title":"again","message":1,"why":"asked",`, "b2"),
		line(4, `"title":"two","message":2,"why":"asked",`, "b3"),
		line(5, `"title":"x","message":3,"why":"w",`, "b4"),
		line(6, `"title":"x","message":9,"why":"w",`, "b5"),
		line(7, `"title":"x","message":2,"why":"w","priority":"P9",`, "b6"))
	if said := r[0]["result"].(map[string]any)["said"]; said != "task T1 begun from founder message #1 and is now your current task" {
		t.Fatalf("%v", r[0])
	}
	if e := r[1]["error"].(map[string]any); e["message"] != "request #1 already opened T1" {
		t.Fatalf("a message opens one task: %v", r[1])
	}
	if said := r[2]["result"].(map[string]any)["said"]; said != "task T2 begun from founder message #2; T1 is still current - `hl task start T2` when you switch" {
		t.Fatalf("%v", r[2])
	}
	for i := 3; i < 5; i++ {
		if r[i]["error"].(map[string]any)["data"].(map[string]any)["code"] != "refused" {
			t.Fatalf("%d: %v", i, r[i])
		}
	}
	if r[5]["error"].(map[string]any)["data"].(map[string]any)["code"] != "bad_request" {
		t.Fatalf("a bad priority: %v", r[5])
	}
	var prio int
	var expects, ev string
	var cur sql.NullInt64
	db.QueryRow("SELECT priority, expects FROM tasks WHERE id = 1").Scan(&prio, &expects)
	db.QueryRow("SELECT current_task FROM agent_state WHERE agent = 'worker'").Scan(&cur)
	db.QueryRow("SELECT payload FROM events WHERE kind = 'task_begin' ORDER BY id LIMIT 1").Scan(&ev)
	if prio != 1 || expects != "sha" || cur.Int64 != 1 || ev != `{"task_id": 1, "chat_request": 1, "why": "asked"}` {
		t.Fatalf("%d %q %v %q", prio, expects, cur, ev)
	}
}
