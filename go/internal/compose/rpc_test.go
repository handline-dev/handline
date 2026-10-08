package compose

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/sessionrpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/transcript"
)

type founder struct{}

func (founder) AgentOf(int) string         { return "" }
func (founder) FounderKeyOK(k string) bool { return k == "key" }

// call serves one connection and returns the replies to the calls given (after the hello).
func call(t *testing.T, srv *rpc.Server, calls ...string) []map[string]any {
	t.Helper()
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
	defer func() { cl.Close(); <-done }()
	rd := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(30 * time.Second))
	var out []map[string]any
	for _, line := range append([]string{`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"key"}}`}, calls...) {
		if _, err := cl.Write([]byte(line + "\n")); err != nil {
			t.Fatal(err)
		}
		b, err := rd.ReadBytes('\n')
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		json.Unmarshal(b, &m)
		out = append(out, m)
	}
	return out[1:]
}

func TestTheSessionMethodsWakeInterruptAndStopAStructuredAgentThroughTheRealWiring(t *testing.T) {
	p, _, _, _ := wired(t)
	srv := rpc.NewServer(founder{}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	srv.DB = p.Sink.DB
	sessionrpc.Register(srv, p.SessionRPC(p.Run.cfg))
	var mu sync.Mutex
	var errs []string // an exit the kill asked for is no error event
	p.Sink.Publish = func(e transcript.Event) {
		if e.Kind == "error" {
			mu.Lock()
			errs = append(errs, e.Kind)
			mu.Unlock()
		}
	}
	one := func(method, params string) map[string]any {
		return call(t, srv, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":%s}`, method, params))[0]
	}
	if r := one("session.wake", `{"agent":"alice","request_id":"w1"}`); r["result"] == nil {
		t.Fatalf("wake: %v", r)
	}
	if !p.Run.Alive("alice") {
		t.Fatal("the wake started the session")
	}
	if !p.Run.WaitIdle("alice", 10*time.Second) { // the wake's first message runs a turn
		t.Fatal("the turn ends")
	}
	if r := one("session.interrupt", `{"agent":"alice","request_id":"i1"}`); r["error"] == nil {
		t.Fatalf("an idle session has no turn to interrupt: %v", r)
	}
	if r := one("session.stop", `{"agent":"alice","mode":"close","request_id":"s1"}`); r["result"] == nil {
		t.Fatalf("stop: %v", r)
	}
	if p.Run.Alive("alice") {
		t.Fatal("the close ended the session")
	}
	if r := one("session.wake", `{"agent":"alice","request_id":"w2"}`); r["result"] == nil {
		t.Fatalf("wake again: %v", r)
	}
	if r := one("session.stop", `{"agent":"alice","mode":"kill","request_id":"k1"}`); r["result"] == nil {
		t.Fatalf("kill: %v", r)
	}
	if p.Run.Alive("alice") {
		t.Fatal("the kill ended the session")
	}
	time.Sleep(200 * time.Millisecond) // the exit callback runs after the kill returns
	mu.Lock()
	defer mu.Unlock()
	if len(errs) != 0 {
		t.Fatalf("a requested kill was reported as an exit error: %v", errs)
	}
	if r := one("session.park", `{"agent":"alice","request_id":"p1"}`); r["error"] == nil {
		t.Fatalf("park is the policy tick's and is refused with its words: %v", r)
	}
	if r := one("session.start", `{"agent":"bob","request_id":"t1"}`); r["error"] == nil {
		t.Fatalf("a terminal agent is not started here: %v", r)
	}
}
