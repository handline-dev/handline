package taskwrites

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"fmt"
	"net"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

type auth struct{ agent string }

func (a auth) AgentOf(int) string     { return a.agent }
func (auth) FounderKeyOK(string) bool { return false }

// call serves one connection as agent `as` over db and returns its replies to the calls given.
func call(t *testing.T, srv *rpc.Server, calls ...string) []map[string]any {
	t.Helper()
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
	defer func() { cl.Close(); <-done }()
	r := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(10 * time.Second))
	var out []map[string]any
	for _, line := range append([]string{`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1}}`}, calls...) {
		if _, err := cl.Write([]byte(line + "\n")); err != nil {
			t.Fatal(err)
		}
		b, err := r.ReadBytes('\n')
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		json.Unmarshal(b, &m)
		out = append(out, m)
	}
	return out[1:]
}

func TestMemosAreAddedEditedAndDeletedByTheirAuthorOrTheFounder(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'worker', 'manager', 1)"); err != nil {
		t.Fatal(err)
	}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s)
		return s
	}
	r := call(t, mk("worker"),
		`{"jsonrpc":"2.0","id":2,"method":"task.memo.add","params":{"task":1,"text":"first","request_id":"a1"}}`,
		`{"jsonrpc":"2.0","id":3,"method":"task.memo.add","params":{"task":1,"text":"first","request_id":"a1"}}`,
		`{"jsonrpc":"2.0","id":4,"method":"task.memo.edit","params":{"task":1,"memo":1,"text":"second","request_id":"a2"}}`,
		`{"jsonrpc":"2.0","id":5,"method":"task.memo.add","params":{"task":9,"text":"x","request_id":"a3"}}`)
	res := r[0]["result"].(map[string]any)
	if res["memo"].(float64) != 1 || res["said"] != "memo 1 added to T1" {
		t.Fatalf("%v", r[0])
	}
	if r[1]["result"].(map[string]any)["replayed"] != true {
		t.Fatalf("a retry replays: %v", r[1])
	}
	if r[2]["result"] == nil {
		t.Fatalf("the author edits: %v", r[2])
	}
	if e := r[3]["error"].(map[string]any); e["data"].(map[string]any)["code"] != "not_found" {
		t.Fatalf("an unknown task is not_found: %v", r[3])
	}
	var n int
	var body string
	db.QueryRow("SELECT COUNT(*), MAX(body) FROM task_memos").Scan(&n, &body)
	if n != 1 || body != "second" {
		t.Fatalf("%d %q", n, body)
	}
	other := call(t, mk("manager"),
		`{"jsonrpc":"2.0","id":2,"method":"task.memo.delete","params":{"task":1,"memo":1,"request_id":"b1"}}`)
	if e := other[0]["error"].(map[string]any); e["data"].(map[string]any)["code"] != "refused" {
		t.Fatalf("another agent may not delete it: %v", other[0])
	}
	if got := call(t, mk("worker"), `{"jsonrpc":"2.0","id":2,"method":"task.memo.delete","params":{"task":1,"memo":1,"request_id":"b2"}}`); got[0]["result"] == nil {
		t.Fatalf("the author deletes: %v", got[0])
	}
	db.QueryRow("SELECT COUNT(*) FROM task_memos").Scan(&n)
	if n != 0 {
		t.Fatal("the memo is still there")
	}
}

func TestReopenSendsADoneTaskBackOnTheAskersWordAndAReason(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, done_evidence, done_at, done_via) VALUES(1, 't', 'worker', 'manager', 1, 'sha', 2, 'reason')"); err != nil {
		t.Fatal(err)
	}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s)
		return s
	}
	line := func(id int, task int, note, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.reopen","params":{"task":%d,"note":%q,"request_id":%q}}`, id, task, note, rid)
	}
	other := call(t, mk("worker"), line(2, 1, "wrong", "w1"))
	if e := other[0]["error"].(map[string]any)["data"].(map[string]any); e["code"] != "refused" {
		t.Fatalf("only the asker or the founder reopens: %v", other[0])
	}
	r := call(t, mk("manager"), line(2, 1, "  ", "m0"), line(3, 9, "x", "m1"), line(4, 1, "the result was wrong", "m2"), line(5, 1, "again", "m3"))
	if r[0]["error"] == nil || r[1]["error"] == nil {
		t.Fatalf("an empty reason and an unknown task are refused: %v %v", r[0], r[1])
	}
	if r[2]["result"].(map[string]any)["said"] != "task T1 reopened by manager: the result was wrong" {
		t.Fatalf("%v", r[2])
	}
	if r[3]["error"] == nil {
		t.Fatalf("a task not done has nothing to reopen: %v", r[3])
	}
	var ev sql.NullString
	var n int
	db.QueryRow("SELECT COUNT(*) FROM tasks WHERE id = 1 AND done_evidence IS NULL").Scan(&n)
	db.QueryRow("SELECT payload FROM events WHERE kind = 'reopen_task'").Scan(&ev)
	if n != 1 || ev.String != `{"task_id": 1, "reason": "the result was wrong", "after_request": 0}` {
		t.Fatalf("%d %q", n, ev.String)
	}
}
