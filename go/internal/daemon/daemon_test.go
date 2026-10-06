package daemon

import (
	"bufio"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

type fakeAuth struct{ agent, key string }

func (f fakeAuth) AgentOf(pid int) string       { return f.agent }
func (f fakeAuth) FounderKeyOK(key string) bool { return f.key != "" && key == f.key }

func start(t *testing.T, a fakeAuth, listen string) *Daemon {
	t.Helper()
	dir, err := os.MkdirTemp("", "hl")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), SocketFile: filepath.Join(dir, "d.sock"),
		Listen: listen + "", Auth: a})
	if err != nil {
		if strings.Contains(err.Error(), "operation not permitted") {
			t.Skipf("this environment will not bind a socket: %v", err)
		}
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d
}

type client struct {
	c net.Conn
	r *bufio.Reader
}

func dial(t *testing.T, d *Daemon) *client {
	t.Helper()
	c, err := net.Dial("unix", d.cfg.SocketFile)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	return &client{c, bufio.NewReader(c)}
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

const hello = `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t/1","protocol":1}}`

func TestSocketIsPrivateAndAStaleOneIsReplaced(t *testing.T) {
	d := start(t, fakeAuth{}, "")
	st, err := os.Stat(d.cfg.SocketFile)
	if err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("%v %v", st, err)
	}
	if _, err := Start(Config{Home: d.cfg.Home, DBFile: d.cfg.DBFile, SocketFile: d.cfg.SocketFile, Auth: fakeAuth{}}); err == nil {
		t.Fatal("a second daemon took a live socket")
	}
	d.Close()
	os.WriteFile(d.cfg.SocketFile, nil, 0o600) // a stale file where the socket was
	d2, err := Start(Config{Home: d.cfg.Home, DBFile: d.cfg.DBFile, SocketFile: d.cfg.SocketFile, Auth: fakeAuth{}})
	if err != nil {
		t.Fatal(err)
	}
	d2.Close()
}

func TestNetworkListenerSpeaksHelloOverTLSWebSocketAndKnowsNoOne(t *testing.T) {
	// the network door alone: no unix socket, so this runs where a sandbox will not bind one
	d := &Daemon{conns: map[net.Conn]struct{}{}}
	cfg := Config{Listen: "127.0.0.1:0", Auth: fakeAuth{key: "k", agent: "a"}, Log: func(string, ...any) {}}
	nl, err := listenNetwork(cfg, d)
	if err != nil {
		t.Fatal(err)
	}
	d.net = nl
	t.Cleanup(d.Close)
	dialer := websocket.Dialer{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}} // the pairing QR carries the fingerprint; here it is checked below
	ws, resp, err := dialer.Dial("wss://"+d.NetworkAddr()+"/rpc", nil)
	if err != nil {
		t.Fatalf("%v %v", err, resp)
	}
	defer ws.Close()
	if fp := sha256hex(ws.UnderlyingConn().(*tls.Conn).ConnectionState().PeerCertificates[0].Raw); fp != d.NetworkFingerprint() {
		t.Fatalf("fingerprint %s != %s", fp, d.NetworkFingerprint())
	}
	ask := func(msg string) map[string]any {
		ws.WriteMessage(websocket.TextMessage, []byte(msg))
		ws.SetReadDeadline(time.Now().Add(5 * time.Second))
		_, b, err := ws.ReadMessage()
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		json.Unmarshal(b, &m)
		return m
	}
	// neither a founder key nor a peer pid means anything over the network
	r := ask(`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"k"}}`)["result"].(map[string]any)
	if r["role"] != "anonymous" || r["transport"] != "network" {
		t.Fatalf("%v", r)
	}
	if _, code := errOf(t, ask(`{"jsonrpc":"2.0","id":2,"method":"hello","params":{"client":"t","protocol":1,"token":"x"}}`)); code != "refused" {
		t.Fatal(code)
	}
}

func sha256hex(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}
