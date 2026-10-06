package rpc

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"net"
	"strings"
	"testing"
	"time"
)

type fakeAuth struct{ agent, key string }

func (f fakeAuth) AgentOf(pid int) string       { return f.agent }
func (f fakeAuth) FounderKeyOK(key string) bool { return f.key != "" && key == f.key }

type client struct {
	c net.Conn
	r *bufio.Reader
}

// dial serves one connection of a fresh server over an in-process pipe.
func dial(t *testing.T, a fakeAuth) *client {
	t.Helper()
	cl, sv := net.Pipe()
	srv := NewServer(a, "abc1234", 7, "local", func(string, ...any) {})
	done := make(chan struct{})
	go func() { srv.Serve(NewLineTransport(sv), 0); sv.Close(); close(done) }()
	t.Cleanup(func() { cl.Close(); <-done })
	return &client{cl, bufio.NewReader(cl)}
}

func (c *client) raw(t *testing.T, line string) map[string]any {
	t.Helper()
	c.c.SetDeadline(time.Now().Add(5 * time.Second))
	if _, err := c.c.Write([]byte(line + "\n")); err != nil {
		t.Fatal(err)
	}
	return c.read(t)
}

func (c *client) read(t *testing.T) map[string]any {
	t.Helper()
	b, err := c.r.ReadBytes('\n')
	if err != nil {
		t.Fatalf("no reply: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func errOf(t *testing.T, m map[string]any) (int, string) {
	t.Helper()
	e, ok := m["error"].(map[string]any)
	if !ok {
		t.Fatalf("no error in %v", m)
	}
	return int(e["code"].(float64)), e["data"].(map[string]any)["code"].(string)
}

const helloLine = `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t/1","protocol":1}}`

func TestHelloReportsVersionSchemaRoleAndMethods(t *testing.T) {
	c := dial(t, fakeAuth{})
	r := c.raw(t, helloLine)["result"].(map[string]any)
	if r["protocol"].(float64) != 1 || r["min_client"].(float64) != 1 || r["role"] != "anonymous" || r["transport"] != "local" {
		t.Fatalf("%v", r)
	}
	if int(r["schema"].(float64)) != 7 {
		t.Fatalf("schema %v", r["schema"])
	}
	if r["daemon"] != "abc1234" {
		t.Fatal("no daemon sha")
	}
	ms := r["methods"].([]any)
	allowed := map[string]bool{}
	for _, m := range ms {
		mm := m.(map[string]any)
		allowed[mm["name"].(string)] = mm["allowed"].(bool)
	}
	if !allowed["hello"] || allowed["whoami"] {
		t.Fatalf("an anonymous caller may call hello only: %v", allowed)
	}
}

func TestRolesComeFromProofOnly(t *testing.T) {
	a := fakeAuth{key: "k"}
	c := dial(t, a)
	r := c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"k"}}`)["result"].(map[string]any)
	if r["role"] != "founder" {
		t.Fatalf("%v", r)
	}
	if w := c.raw(t, `{"jsonrpc":"2.0","id":2,"method":"whoami"}`)["result"].(map[string]any); w["role"] != "founder" {
		t.Fatalf("%v", w)
	}
	c = dial(t, fakeAuth{key: "k"})
	if r := c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"wrong"}}`)["result"].(map[string]any); r["role"] != "anonymous" {
		t.Fatalf("a wrong key is no proof: %v", r)
	}
	// an agent's session wins over a founder key it somehow held
	c = dial(t, fakeAuth{agent: "agent-x", key: "k"})
	r = c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"k"}}`)["result"].(map[string]any)
	if r["role"] != "agent" || r["caller"].(map[string]any)["name"] != "agent-x" {
		t.Fatalf("%v", r)
	}
}

func TestErrors(t *testing.T) {
	a := fakeAuth{}
	for _, tc := range []struct {
		name, line string
		rpc        int
		code       string
	}{
		{"not json", `{nope`, -32700, "bad_request"},
		{"batch", `[{"jsonrpc":"2.0","id":1,"method":"hello"}]`, -32600, "bad_request"},
		{"not 2.0", `{"id":1,"method":"hello"}`, -32600, "bad_request"},
		{"bool id", `{"jsonrpc":"2.0","id":true,"method":"hello"}`, -32600, "bad_request"},
		{"method not a string", `{"jsonrpc":"2.0","id":1,"method":3}`, -32600, "bad_request"},
		{"params not an object", `{"jsonrpc":"2.0","id":1,"method":"hello","params":[1]}`, -32602, "bad_request"},
		{"hello first", `{"jsonrpc":"2.0","id":1,"method":"whoami"}`, -32600, "bad_request"},
		{"hello without client", `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"protocol":1}}`, -32602, "bad_request"},
		{"protocol a float", `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1.0}}`, -32602, "bad_request"},
		{"protocol a bool", `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":true}}`, -32602, "bad_request"},
		{"protocol too old", `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":0}}`, -32002, "refused"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rpc, code := errOf(t, dial(t, a).raw(t, tc.line))
			if rpc != tc.rpc || code != tc.code {
				t.Fatalf("got %d %s", rpc, code)
			}
		})
	}
}

func TestErrorsAfterHello(t *testing.T) {
	c := dial(t, fakeAuth{})
	c.raw(t, helloLine)
	if rpc, code := errOf(t, c.raw(t, `{"jsonrpc":"2.0","id":2,"method":"no.such"}`)); rpc != -32601 || code != "bad_request" {
		t.Fatalf("%d %s", rpc, code)
	}
	m := c.raw(t, `{"jsonrpc":"2.0","id":3,"method":"whoami"}`)
	rpc, code := errOf(t, m)
	data := m["error"].(map[string]any)["data"].(map[string]any)
	if rpc != -32002 || code != "refused" || data["gate"] != "role" || data["reason"] == "" {
		t.Fatalf("%v", m)
	}
	if m["id"].(float64) != 3 {
		t.Fatal("the error carries the request's id")
	}
	// the connection stays usable after errors, and a string id is echoed
	if r := c.raw(t, `{"jsonrpc":"2.0","id":"abc","method":"hello","params":{"client":"t","protocol":1}}`); r["id"] != "abc" || r["result"] == nil {
		t.Fatalf("%v", r)
	}
}

func TestPolicyOfTheConnection(t *testing.T) {
	a := fakeAuth{}
	// a notification is not answered; the next call is
	c := dial(t, a)
	c.c.Write([]byte(`{"jsonrpc":"2.0","method":"hello","params":{"client":"t","protocol":1}}` + "\n"))
	if r := c.raw(t, `{"jsonrpc":"2.0","id":9,"method":"hello","params":{"client":"t","protocol":1}}`); r["id"].(float64) != 9 {
		t.Fatalf("%v", r)
	}
	// a line past the limit is bad_request and the connection ends
	big := dial(t, a)
	big.c.SetDeadline(time.Now().Add(5 * time.Second))
	go big.c.Write([]byte(strings.Repeat("x", (1<<20)+70000)))
	if rpc, code := errOf(t, big.read(t)); rpc != -32600 || code != "bad_request" {
		t.Fatalf("%d %s", rpc, code)
	}
	// a legacy verb is told it is not served here and hung up on
	l := dial(t, a)
	if r := l.raw(t, `{"verb":"wake","agent":"x"}`); r["ok"] != false {
		t.Fatalf("%v", r)
	}
}

// A client that shuts its write side after one object needs no newline.
func TestLastLineNeedsNoNewline(t *testing.T) {
	cl, sv := net.Pipe()
	go func() { cl.Write([]byte(helloLine)); cl.Close() }()
	tr := NewLineTransport(sv)
	got, err := tr.ReadMessage(time.Second)
	if err != nil || !bytes.Equal(got, []byte(helloLine)) {
		t.Fatalf("%s %v", got, err)
	}
	if _, err := tr.ReadMessage(time.Second); err != io.EOF {
		t.Fatalf("%v", err)
	}
}
