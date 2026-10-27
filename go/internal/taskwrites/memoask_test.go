package taskwrites

import (
	"fmt"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestACommentNamingHumanOpensOneAskAndEachClosingEndsIt(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'worker', 'manager', 1)"); err != nil {
		t.Fatal(err)
	}
	mk := func(agent string) *rpc.Server { return serverFor(t, db, agent) }
	n := 0
	rid := func() string { n++; return fmt.Sprintf("r%d", n) }
	add := func(as, text string) int64 {
		r := call(t, mk(as), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.comment.add","params":{"task":1,"text":%q,"request_id":%q}}`, text, rid()))
		res, _ := r[0]["result"].(map[string]any)
		if res == nil {
			t.Fatalf("%s: %v", text, r[0])
		}
		return int64(res["memo"].(float64))
	}
	state := func(mid int64) string { // the state of the ask comment mid raised, "" for none
		var s string
		db.QueryRow("SELECT state FROM requests WHERE memo_id = ?", mid).Scan(&s)
		return s
	}
	if mid := add("worker", "no one named here, email a@human.com"); state(mid) != "" {
		t.Fatalf("a comment that does not name `@human` opened an ask: %q", state(mid))
	}
	m1 := add("worker", "@human which one?")
	var target, requester string
	var linked int
	db.QueryRow("SELECT target, requester, (SELECT COUNT(*) FROM task_requests WHERE request_id = requests.id) FROM requests WHERE memo_id = ?", m1).Scan(&target, &requester, &linked)
	if state(m1) != "queued" || target != "human" || requester != "worker" || linked != 1 {
		t.Fatalf("ask: %q %s %s %d", state(m1), target, requester, linked)
	}
	edit := func(mid int64, text string) {
		call(t, mk("worker"), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.comment.edit","params":{"task":1,"memo":%d,"text":%q,"request_id":%q}}`, mid, text, rid()))
	}
	edit(m1, "@human which one, A or B?")
	var body string
	var cnt int
	db.QueryRow("SELECT body FROM requests WHERE memo_id = ?", m1).Scan(&body)
	db.QueryRow("SELECT COUNT(*) FROM requests WHERE memo_id = ?", m1).Scan(&cnt)
	if cnt != 1 || body != "@human which one, A or B?" {
		t.Fatalf("an edit that keeps `@human` keeps the one ask: %d %q", cnt, body)
	}
	edit(m1, "never mind")
	if state(m1) != "withdrawn" {
		t.Fatalf("an edit that drops `@human`: %q", state(m1))
	}
	m2 := add("worker", "@human again")
	call(t, mk("worker"), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.comment.delete","params":{"task":1,"memo":%d,"request_id":%q}}`, m2, rid()))
	if state(m2) != "withdrawn" {
		t.Fatalf("a deleted comment: %q", state(m2))
	}
	m3 := add("worker", "@human third")
	add("", "my answer")
	if state(m3) != "landed" {
		t.Fatalf("a founder comment on the task closes it: %q", state(m3))
	}
}

// An ask a comment raised does not outlive its task: done or discarded closes it (rule 5 of task.comment.add).
func TestTheAskOfACommentEndsWithItsTask(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, expects) VALUES(1, 'a', 'worker', 'manager', 1, 'report'), (2, 'b', 'worker', 'manager', 1, 'report')"); err != nil {
		t.Fatal(err)
	}
	srv := rpc.NewServer(auth{"worker"}, "x", store.LatestVersion(), "local", t.Logf)
	srv.DB = db
	Register(srv, t.TempDir(), &fakeTeller{}, nil)
	for i, c := range []struct {
		task           int
		method, params string
	}{
		{1, "task.done", `"evidence":"shipped"`},
		{2, "task.discard", `"reason":"not needed"`},
	} {
		r := call(t, srv, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.comment.add","params":{"task":%d,"text":"@human which?","request_id":"a%d"}}`, c.task, i))
		mid := int64(r[0]["result"].(map[string]any)["memo"].(float64))
		var state string
		db.QueryRow("SELECT state FROM requests WHERE memo_id = ?", mid).Scan(&state)
		if state != "queued" {
			t.Fatalf("%s: the ask is %q before", c.method, state)
		}
		r = call(t, srv, fmt.Sprintf(`{"jsonrpc":"2.0","id":3,"method":"%s","params":{"task":%d,%s,"request_id":"e%d"}}`, c.method, c.task, c.params, i))
		if r[0]["error"] != nil {
			t.Fatalf("%s: %v", c.method, r[0])
		}
		db.QueryRow("SELECT state FROM requests WHERE memo_id = ?", mid).Scan(&state)
		if state == "queued" {
			t.Fatalf("%s left the ask open", c.method)
		}
	}
}
