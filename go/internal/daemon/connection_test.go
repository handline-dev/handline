package daemon

import (
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestConnectionGetIsTheFoundersAndSaysADaemonWithNoListenerHasNone(t *testing.T) {
	t.Setenv("HANDLINE_HEAVY_SLOTS", "3")
	t.Setenv("HANDLINE_HEAVY_DIR", "/slots")
	d := start(t, fakeAuth{key: "k"}, "")
	if err := os.WriteFile(filepath.Join(d.cfg.Home, "config.yaml"), []byte("dev_mode: true\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, d)
	c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"k"}}`)
	res, _ := c.raw(t, `{"jsonrpc":"2.0","id":2,"method":"connection.get","params":{}}`)["result"].(map[string]any)
	slots, _ := res["heavy_slots"].(map[string]any)
	if res["served_by"] != "daemon" || res["listener"] != nil || res["dev_mode"] != true || slots["slots"] != 3.0 || slots["dir"] != "/slots" {
		t.Fatalf("%v", res)
	}
	agent := dial(t, start(t, fakeAuth{agent: "worker"}, ""))
	agent.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1}}`)
	if r := agent.raw(t, `{"jsonrpc":"2.0","id":2,"method":"connection.get","params":{}}`); r["error"] == nil {
		t.Fatalf("an agent was served connection.get: %v", r)
	}
}

func TestAGatewayAnswersConnectionGetItselfWithItsListener(t *testing.T) {
	d, f, _ := startGateway(t, "")
	token, _ := pair(t, d, "phone")
	c := helloAs(t, d, token)
	before := f.dials.Load()
	res, _ := c.ask(`{"jsonrpc":"2.0","id":2,"method":"connection.get","params":{}}`)["result"].(map[string]any)
	l, _ := res["listener"].(map[string]any)
	if res["served_by"] != "gateway" || l == nil || l["address"] != d.net.ln.Addr().String() || l["encryption"] != "tls" || l["fingerprint"] != d.net.Fingerprint() ||
		l["tailscale_host"] != nil {
		t.Fatalf("%v", res)
	}
	if f.dials.Load() != before {
		t.Fatal("connection.get was relayed: the gateway's own listener is what it answers")
	}
}

func TestAnInheritedConnectionWaitsForItsFirstByteBeforeTheLimitApplies(t *testing.T) {
	a, b := net.Pipe()
	defer a.Close()
	defer b.Close()
	c := &inherited{Conn: a}
	c.SetReadDeadline(time.Now().Add(-time.Second))
	go b.Write([]byte("x"))
	if n, err := c.Read(make([]byte, 1)); n != 1 || err != nil {
		t.Fatalf("before its first byte a read waits past the deadline: got %d, %v", n, err)
	}
	c.SetReadDeadline(time.Now().Add(-time.Second))
	if _, err := c.Read(make([]byte, 1)); !os.IsTimeout(err) {
		t.Fatalf("after its first byte the deadline holds: got %v", err)
	}
}
