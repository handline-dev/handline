package daemon

import (
	"bufio"
	"encoding/json"
	"net"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
)

// pidAuth knows process 1 as the agent "a" and holds the founder key "k".
type pidAuth struct{}

func (pidAuth) AgentOf(pid int) string {
	if pid == 1 {
		return "a"
	}
	return ""
}
func (pidAuth) FounderKeyOK(key string) bool { return key == "k" }

// startStream is a gateway with a fake relay and no socket file, whose callers are told apart by pid.
func startStream(t *testing.T) (*Daemon, *fakePython) {
	t.Helper()
	dir := t.TempDir()
	pre, err := store.Open(filepath.Join(dir, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	pre.Close()
	crt, key := writeCert(t, dir)
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Listen: "127.0.0.1:0", CertFile: crt, KeyFile: key, Gateway: true,
		RelaySocket: filepath.Join(dir, "relay.sock"), PidFile: filepath.Join(dir, "protected", "gateway", "pid"), Auth: pidAuth{}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	f := &fakePython{t: t, script: echo, closed: make(chan struct{}, 16)}
	d.net.rs.Relay.Dial = f.dial
	return d, f
}

// pipeConn is a connection to the local server over a pipe, so no socket file is bound.
type pipeConn struct {
	t *testing.T
	c net.Conn
	r *bufio.Reader
}

// pipeTo says hello as the founder (pid 0, the key) or, with no key, as the agent (pid 1).
func pipeTo(t *testing.T, d *Daemon, key string) *pipeConn {
	t.Helper()
	srvEnd, cli := net.Pipe()
	pid := 0
	if key == "" {
		pid = 1
	}
	go d.local.Serve(rpc.NewLineTransport(srvEnd), pid)
	t.Cleanup(func() { cli.Close() })
	p := &pipeConn{t, cli, bufio.NewReader(cli)}
	if r := p.ask(`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"` + key + `"}}`); r["result"] == nil {
		t.Fatalf("hello: %v", r)
	}
	return p
}

func (p *pipeConn) say(line string) {
	p.c.SetWriteDeadline(time.Now().Add(5 * time.Second))
	if _, err := p.c.Write([]byte(line + "\n")); err != nil {
		p.t.Fatal(err)
	}
}

func (p *pipeConn) next() map[string]any {
	p.t.Helper()
	p.c.SetReadDeadline(time.Now().Add(5 * time.Second))
	b, err := p.r.ReadBytes('\n')
	if err != nil {
		p.t.Fatalf("nothing came: %v", err)
	}
	var m map[string]any
	json.Unmarshal(b, &m)
	return m
}

func (p *pipeConn) ask(line string) map[string]any { p.say(line); return p.next() }

// quiet is true when nothing arrives for a moment.
func (p *pipeConn) quiet() bool {
	p.c.SetReadDeadline(time.Now().Add(700 * time.Millisecond))
	_, err := p.r.ReadBytes('\n')
	return err != nil
}

func eventOf(t *testing.T, m map[string]any) map[string]any {
	t.Helper()
	if m["method"] != "event" {
		t.Fatalf("not an event: %v", m)
	}
	return m["params"].(map[string]any)
}

func TestDevicesEventsReachAFounderSubscriber(t *testing.T) {
	d, _ := startStream(t)
	f := pipeTo(t, d, "k")
	sub := f.ask(`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["devices"]}}`)
	if sub["result"] == nil {
		t.Fatalf("%v", sub)
	}
	pr := f.ask(`{"jsonrpc":"2.0","id":3,"method":"device.pair","params":{"request_id":"p1"}}`)["result"].(map[string]any)
	redeem := dialNet(t, d).ask(`{"jsonrpc":"2.0","id":1,"method":"device.redeem","params":{"code":"` + pr["code"].(string) + `","name":"phone","request_id":"r1"}}`)["result"].(map[string]any)
	id := redeem["device"].(string)

	ev := eventOf(t, f.next())
	if ev["type"] != "device.paired" || ev["topic"] != "devices" || ev["cursor"] != float64(0) || ev["agent"] != nil || ev["actor"] != nil {
		t.Fatalf("%v", ev)
	}
	dev := ev["data"].(map[string]any)
	if dev["id"] != id || dev["name"] != "phone" || dev["role"] != "member" || dev["connected"] != false || dev["last_seen"] != nil {
		t.Fatalf("%v", dev)
	}
	f.say(`{"jsonrpc":"2.0","id":4,"method":"device.revoke","params":{"device":"` + id + `","request_id":"v1"}}`)
	// the reply and the event are two messages, in either order
	var got map[string]any
	for i := 0; i < 2; i++ {
		m := f.next()
		if m["method"] == "event" {
			got = eventOf(t, m)
		} else if m["result"] == nil {
			t.Fatalf("%v", m)
		}
	}
	if got["type"] != "device.revoked" || got["data"].(map[string]any)["device"] != id || got["actor"].(map[string]any)["kind"] != "user" {
		t.Fatalf("%v", got)
	}
}

func TestDevicesIsSubscribedAloneAndOnlyByTheFounder(t *testing.T) {
	d, f := startStream(t)
	fo := pipeTo(t, d, "k")
	for _, topics := range []string{`["devices","tasks"]`, `["tasks","devices"]`} {
		e := fo.ask(`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":` + topics + `}}`)["error"].(map[string]any)
		if e["code"].(float64) != -32602 || e["data"].(map[string]any)["field"] != "topics" {
			t.Fatalf("%s: %v", topics, e)
		}
	}
	if e := fo.ask(`{"jsonrpc":"2.0","id":3,"method":"unsubscribe","params":{"topics":["devices","hr"]}}`)["error"]; e == nil {
		t.Fatal("unsubscribe with devices and another topic")
	}
	m := helloAs(t, d, mustPair(t, d))
	if r := m.ask(`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["devices"]}}`); gate(r) != "role" {
		t.Fatalf("%v", r)
	}
	if r := m.ask(`{"jsonrpc":"2.0","id":3,"method":"subscribe","params":{"topics":["devices","tasks"]}}`)["error"].(map[string]any); r["data"].(map[string]any)["field"] != "topics" {
		t.Fatalf("%v", r)
	}
	if r := m.ask(`{"jsonrpc":"2.0","id":4,"method":"unsubscribe","params":{"topics":["devices"]}}`); r["result"] == nil {
		t.Fatalf("a stop is not refused: %v", r)
	}
	f.onlyHelloReached(t)
}

func TestEveryOtherSubscribeOfADeviceIsServedHereFromTheLog(t *testing.T) {
	d, f := startStream(t)
	m := helloAs(t, d, mustPair(t, d))
	for _, call := range []string{
		`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["tasks","chat"],"agent":"a"}}`,
		`{"jsonrpc":"2.0","id":3,"method":"unsubscribe","params":{"topics":["tasks"]}}`,
	} {
		r := m.ask(call)
		if r["result"] == nil || r["result"].(map[string]any)["method"] != nil {
			t.Fatalf("%s: %v", call, r)
		}
	}
	for _, call := range []string{
		`{"jsonrpc":"2.0","id":4,"method":"subscribe","params":{"topics":"nope"}}`,
		`{"jsonrpc":"2.0","id":5,"method":"subscribe","params":{}}`,
	} {
		e, _ := m.ask(call)["error"].(map[string]any)
		if e == nil || e["code"].(float64) != -32602 || e["data"].(map[string]any)["field"] != "topics" {
			t.Fatalf("%s: %v", call, e)
		}
	}
	f.onlyHelloReached(t)
}

func TestARowAppendedAfterASubscribeReachesTheSubscriber(t *testing.T) {
	d, _ := startStream(t)
	fo := pipeTo(t, d, "k")
	r := fo.ask(`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["accounts"]}}`)["result"].(map[string]any)
	if r["cursor"] != float64(0) {
		t.Fatalf("%v", r)
	}
	ev, err := d.hub.Append(stream.Entry{Topic: "accounts", Type: "account.changed", Data: map[string]any{"name": "main"}})
	if err != nil {
		t.Fatal(err)
	}
	got := eventOf(t, fo.next())
	if got["cursor"] != float64(ev.Cursor) || got["topic"] != "accounts" || got["type"] != "account.changed" || got["agent"] != nil {
		t.Fatalf("%v", got)
	}
	fo.ask(`{"jsonrpc":"2.0","id":3,"method":"unsubscribe","params":{"topics":["accounts"]}}`)
	if _, err := d.hub.Append(stream.Entry{Topic: "accounts", Type: "account.changed", Data: map[string]any{"name": "main"}}); err != nil {
		t.Fatal(err)
	}
	if !fo.quiet() {
		t.Fatal("an event came after unsubscribe")
	}
}

func TestUnsubscribingDevicesStopsItsEvents(t *testing.T) {
	d, _ := startStream(t)
	fo := pipeTo(t, d, "k")
	fo.ask(`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["devices"]}}`)
	if r := fo.ask(`{"jsonrpc":"2.0","id":3,"method":"unsubscribe","params":{"topics":["devices"]}}`)["result"]; len(r.(map[string]any)) != 0 {
		t.Fatalf("%v", r)
	}
	code, _, _, _ := d.dev.Pair(time.Minute)
	dialNet(t, d).ask(`{"jsonrpc":"2.0","id":1,"method":"device.redeem","params":{"code":"` + code + `","name":"p","request_id":"r"}}`)
	if !fo.quiet() {
		t.Fatal("an event came after unsubscribe")
	}
}

func TestSubscribeForAnotherTopicOnTheGatewaysOwnSocketIsAnsweredAndSilent(t *testing.T) {
	d, f := startStream(t)
	fo := pipeTo(t, d, "k")
	if r := fo.ask(`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["tasks"]}}`)["result"]; r.(map[string]any)["cursor"] != float64(0) {
		t.Fatalf("%v", r)
	}
	if !fo.quiet() || f.dials.Load() != 0 {
		t.Fatal("the founder's socket relays nothing and is sent nothing")
	}
	for _, params := range []string{`{}`, `{"topics":[]}`, `{"topics":["nope"]}`, `{"topics":[5]}`} {
		e := fo.ask(`{"jsonrpc":"2.0","id":3,"method":"subscribe","params":` + params + `}`)["error"].(map[string]any)
		if e["code"].(float64) != -32602 || e["data"].(map[string]any)["field"] != "topics" {
			t.Fatalf("%s: %v", params, e)
		}
	}
}

func TestAnAgentIsRefusedTheTopicsWhoseReadsAreAMembers(t *testing.T) {
	d, _ := startStream(t)
	ag, fo := pipeTo(t, d, ""), pipeTo(t, d, "k")
	for _, topic := range []string{"chat", "operations"} {
		call := `{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["` + topic + `"]}}`
		e, _ := ag.ask(call)["error"].(map[string]any)
		if e == nil || e["data"].(map[string]any)["code"] != "refused" {
			t.Fatalf("an agent subscribing to %s: %v", topic, e)
		}
		if r := fo.ask(call)["result"]; r == nil {
			t.Fatalf("the founder is refused %s", topic)
		}
	}
	if r := ag.ask(`{"jsonrpc":"2.0","id":3,"method":"subscribe","params":{"topics":["tasks","agents"]}}`)["result"]; r == nil {
		t.Fatal("an agent still follows the topics it may read")
	}
}
