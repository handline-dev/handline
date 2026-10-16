package compose

import (
	"bufio"
	"encoding/json"
	"net"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
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

func (founder) SessionsReadable() error { return nil }
