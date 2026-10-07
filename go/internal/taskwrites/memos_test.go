package taskwrites

import (
	"bufio"
	"encoding/json"
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
