package rpc

import (
	"bufio"
	"database/sql"
	"fmt"
	"net"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

// fakeNet pairs the tokens "t1" and "t2" as devices d1 and d2.
type fakeNet struct{}

func (fakeNet) Know(token string) bool { return token == "t1" || token == "t2" }
func (fakeNet) Login(token string, _ func()) (string, string, func(), bool) {
	if !(fakeNet{}).Know(token) {
		return "", "", nil, false
	}
	return "d" + token[1:], "phone" + token[1:], func() {}, true
}
func (fakeNet) Live(string) bool { return true }

// writeServer is a server over db with test.effect registered; ran counts how often the effect ran.
func writeServer(t *testing.T, db *sql.DB, ran *int, net NetGate) *Server {
	t.Helper()
	srv := NewServer(fakeAuth{key: "k"}, "abc1234", store.LatestVersion(), "local", func(string, ...any) {})
	srv.DB, srv.Net = db, net
	srv.Register("test.effect", Method{Role: "member", Write: true, Handler: func(c *Conn, p map[string]any) (any, error) {
		return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			*ran++
			if p["fail"] == true {
				return nil, BadRequestError("asked to fail", "fail", BadRequest)
			}
			return map[string]any{"n": *ran}, nil
		})
	}})
	return srv
}

// connect serves one connection of srv and says hello with params.
func connect(t *testing.T, srv *Server, hello string) *client {
	t.Helper()
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { srv.Serve(NewLineTransport(sv), 0); sv.Close(); close(done) }()
	t.Cleanup(func() { cl.Close(); <-done })
	c := &client{cl, bufio.NewReader(cl)}
	c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":`+hello+`}`)
	return c
}

func newDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

// writeClient serves one founder connection over a real database.
func writeClient(t *testing.T) (*client, *sql.DB, *int) {
	t.Helper()
	db, ran := newDB(t), new(int)
	return connect(t, writeServer(t, db, ran, nil), `{"client":"t","protocol":1,"key":"k"}`), db, ran
}

func TestEffectIsRecordedWithTheWriteAndAnswersARetryAfterTheReplyWasLost(t *testing.T) {
	c, db, ran := writeClient(t)
	r := c.raw(t, `{"jsonrpc":"2.0","id":2,"method":"test.effect","params":{"request_id":"r1"}}`)["result"].(map[string]any)
	if r["n"].(float64) != 1 || *ran != 1 {
		t.Fatalf("%v ran %d", r, *ran)
	}
	var n int
	db.QueryRow("SELECT COUNT(*) FROM write_effects WHERE request_id = 'r1' AND method = 'test.effect'").Scan(&n)
	if n != 1 {
		t.Fatal("the effect was not recorded")
	}
	// the reply was lost with the daemon: only the effect is left
	if _, err := db.Exec("DELETE FROM rpc_replies"); err != nil {
		t.Fatal(err)
	}
	r = c.raw(t, `{"jsonrpc":"2.0","id":3,"method":"test.effect","params":{"request_id":"r1"}}`)["result"].(map[string]any)
	if r["n"].(float64) != 1 || r["replayed"] != true || *ran != 1 {
		t.Fatalf("a retry repeats nothing and says replayed: %v ran %d", r, *ran)
	}
}

func TestAnEffectThatFailsLeavesNoRecordAndALaterRetryRunsAgain(t *testing.T) {
	c, db, ran := writeClient(t)
	if code, _ := errOf(t, c.raw(t, `{"jsonrpc":"2.0","id":2,"method":"test.effect","params":{"request_id":"r2","fail":true}}`)); code == 0 {
		t.Fatal("no error")
	}
	var n int
	db.QueryRow("SELECT COUNT(*) FROM write_effects").Scan(&n)
	if n != 0 {
		t.Fatal("a failed write left an effect")
	}
	if r := c.raw(t, `{"jsonrpc":"2.0","id":3,"method":"test.effect","params":{"request_id":"r2"}}`)["result"].(map[string]any); r["n"].(float64) != 2 || *ran != 2 {
		t.Fatalf("%v", r)
	}
}

func TestWritesAreKeptPerWriterSoTwoDevicesAndTheFounderMayUseOneRequestID(t *testing.T) {
	db, ran := newDB(t), new(int)
	call := `{"jsonrpc":"2.0","id":2,"method":"test.effect","params":{"request_id":"r1"}}`
	clients := map[string]*client{
		"founder": connect(t, writeServer(t, db, ran, nil), `{"client":"t","protocol":1,"key":"k"}`),
		"d1":      connect(t, writeServer(t, db, ran, fakeNet{}), `{"client":"t","protocol":1,"token":"t1"}`),
		"d2":      connect(t, writeServer(t, db, ran, fakeNet{}), `{"client":"t","protocol":1,"token":"t2"}`),
	}
	want := map[string]float64{"founder": 1, "d1": 2, "d2": 3}
	for _, who := range []string{"founder", "d1", "d2"} {
		r := clients[who].raw(t, call)["result"].(map[string]any)
		if r["n"].(float64) != want[who] || r["replayed"] != nil {
			t.Fatalf("%s: its own write runs once, not another's reply: %v", who, r)
		}
	}
	for _, who := range []string{"founder", "d1", "d2"} {
		r := clients[who].raw(t, call)["result"].(map[string]any)
		if r["n"].(float64) != want[who] || r["replayed"] != true {
			t.Fatalf("%s: a retry replays its own: %v", who, r)
		}
	}
	if *ran != 3 {
		t.Fatalf("each write ran once: %d", *ran)
	}
	rows, err := db.Query("SELECT caller FROM write_effects ORDER BY caller")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var got []string
	for rows.Next() {
		var c string
		rows.Scan(&c)
		got = append(got, c)
	}
	if len(got) != 3 || got[0] != "device:d1" || got[1] != "device:d2" || got[2] != "human" {
		t.Fatalf("kept under the writer: %v", got)
	}
}

func TestACommitThenErrorKeepsTheWritesAndTheRefusalButNoRecord(t *testing.T) {
	db := newDB(t)
	srv := writeServer(t, db, new(int), nil)
	srv.Register("test.keep", Method{Role: "member", Write: true, Handler: func(c *Conn, p map[string]any) (any, error) {
		return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			if _, err := tx.Exec("INSERT OR REPLACE INTO meta(name, value, updated) VALUES('kept', '1', 1)"); err != nil {
				return nil, err
			}
			return nil, &CommitThen{Err: Refused("denied", "", "")}
		})
	}})
	c := connect(t, srv, `{"client":"t","protocol":1,"key":"k"}`)
	for i, rid := range []string{"k1", "k2"} {
		r := c.raw(t, fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"test.keep","params":{"request_id":%q}}`, i+2, rid))
		if r["error"].(map[string]any)["data"].(map[string]any)["code"] != "refused" {
			t.Fatalf("answered refused: %v", r)
		}
	}
	var kept, effects int
	db.QueryRow("SELECT COUNT(*) FROM meta WHERE name = 'kept'").Scan(&kept)
	db.QueryRow("SELECT COUNT(*) FROM write_effects").Scan(&effects)
	if kept == 0 || effects != 0 {
		t.Fatalf("the write stands, nothing is recorded, so a retry is judged again: %d %d", kept, effects)
	}
}
