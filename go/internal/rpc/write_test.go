package rpc

import (
	"bufio"
	"database/sql"
	"net"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

// writeClient serves one founder connection of a server over a real database, with test.effect registered.
func writeClient(t *testing.T) (*client, *sql.DB, *int) {
	t.Helper()
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	cl, sv := net.Pipe()
	srv := NewServer(fakeAuth{key: "k"}, "abc1234", store.LatestVersion(), "local", func(string, ...any) {})
	srv.DB = db
	ran := 0
	srv.Register("test.effect", Method{Role: "founder", Write: true, Handler: func(c *Conn, p map[string]any) (any, error) {
		return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			ran++
			if p["fail"] == true {
				return nil, BadRequestError("asked to fail", "fail", BadRequest)
			}
			return map[string]any{"n": ran}, nil
		})
	}})
	done := make(chan struct{})
	go func() { srv.Serve(NewLineTransport(sv), 0); sv.Close(); close(done) }()
	t.Cleanup(func() { cl.Close(); <-done })
	c := &client{cl, bufio.NewReader(cl)}
	c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"k"}}`)
	return c, db, &ran
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
