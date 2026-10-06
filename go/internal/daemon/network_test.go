package daemon

import (
	"bufio"
	"crypto/tls"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/handline/handline/go/internal/rpc"
)

// startNet is a daemon with only the network door: no unix socket, so this runs where a sandbox will not bind one.
func startNet(t *testing.T) *Daemon {
	t.Helper()
	dir := t.TempDir()
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Listen: "127.0.0.1:0", Auth: fakeAuth{key: "k", agent: "a"}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d
}

type wsClient struct {
	t  *testing.T
	ws *websocket.Conn
}

func dialNet(t *testing.T, d *Daemon) *wsClient {
	t.Helper()
	dialer := websocket.Dialer{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}} // the fingerprint is checked below
	ws, resp, err := dialer.Dial("wss://"+d.NetworkAddr()+"/rpc", nil)
	if err != nil {
		t.Fatalf("%v %v", err, resp)
	}
	t.Cleanup(func() { ws.Close() })
	if fp := sha256hex(ws.UnderlyingConn().(*tls.Conn).ConnectionState().PeerCertificates[0].Raw); fp != d.NetworkFingerprint() {
		t.Fatalf("fingerprint %s != %s", fp, d.NetworkFingerprint())
	}
	return &wsClient{t, ws}
}

func (c *wsClient) ask(msg string) map[string]any {
	c.t.Helper()
	c.ws.WriteMessage(websocket.TextMessage, []byte(msg))
	c.ws.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, b, err := c.ws.ReadMessage()
	if err != nil {
		c.t.Fatal(err)
	}
	var m map[string]any
	json.Unmarshal(b, &m)
	return m
}

func (c *wsClient) closed() bool {
	c.ws.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, _, err := c.ws.ReadMessage()
	return err != nil && !os.IsTimeout(err)
}

var unpaired = map[string]any{"code": float64(-32002), "message": "refused",
	"data": map[string]any{"code": "refused", "gate": "unpaired", "reason": "not paired"}}

// pair makes a code as the founder would and trades it for a token on a connection of its own.
func pair(t *testing.T, d *Daemon, name string) (token, id string) {
	t.Helper()
	code, _, _, _ := d.dev.Pair(time.Minute)
	r := dialNet(t, d).ask(`{"jsonrpc":"2.0","id":1,"method":"device.redeem","params":{"code":"` + code + `","name":"` + name + `","request_id":"r-` + name + `"}}`)
	res, ok := r["result"].(map[string]any)
	if !ok {
		t.Fatalf("redeem: %v", r)
	}
	return res["token"].(string), res["device"].(string)
}

func TestAnUnpairedConnectionReadsNothingForAnyMethodOfTheSpec(t *testing.T) {
	raw, err := os.ReadFile("../../../contract/protocol.json")
	if err != nil {
		t.Fatal(err)
	}
	var spec struct {
		Methods []struct{ Name string } `json:"methods"`
	}
	if err := json.Unmarshal(raw, &spec); err != nil || len(spec.Methods) < 50 {
		t.Fatalf("%v: %d methods", err, len(spec.Methods))
	}
	d := startNet(t)
	c := dialNet(t, d)
	names := []string{"hello", "no.such.method", ""}
	for _, m := range spec.Methods {
		if m.Name != "device.redeem" {
			names = append(names, m.Name)
		}
	}
	for i, name := range names {
		for _, params := range []string{`{}`, `{"client":"t","protocol":1,"key":"k","token":"nope","agent":"a"}`,
			`{"token":"nope"}`, `{"client":"t","protocol":0,"token":"nope"}`} {
			r := c.ask(`{"jsonrpc":"2.0","id":` + string(rune('1'+i%9)) + `,"method":"` + name + `","params":` + params + `}`)
			if !reflect.DeepEqual(r["error"], unpaired) || r["result"] != nil {
				t.Fatalf("%q with %s: %v", name, params, r)
			}
		}
	}
	for _, junk := range []string{`not json`, `[]`, `[{"jsonrpc":"2.0","id":1,"method":"hello"}]`, `{"verb":"status"}`, `{"id":1}`, `{"jsonrpc":"2.0","method":5}`} {
		if r := c.ask(junk); !reflect.DeepEqual(r["error"], unpaired) {
			t.Fatalf("%s: %v", junk, r)
		}
	}
}

func TestPairingMakesAMemberWhoseRevocationClosesTheConnection(t *testing.T) {
	d := startNet(t)
	token, id := pair(t, d, "phone")
	c := dialNet(t, d)
	r := c.ask(`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"token":"` + token + `","key":"k"}}`)["result"].(map[string]any)
	if r["role"] != "member" || r["transport"] != "network" || r["caller"].(map[string]any)["device"] != id {
		t.Fatalf("%v", r)
	}
	// a member is refused what is above it, by role
	e := c.ask(`{"jsonrpc":"2.0","id":2,"method":"devices.list"}`)["error"].(map[string]any)
	if e["data"].(map[string]any)["gate"] != "role" {
		t.Fatalf("%v", e)
	}
	if c.ask(`{"jsonrpc":"2.0","id":3,"method":"whoami"}`)["result"] == nil {
		t.Fatal("a member cannot call whoami")
	}
	if ds, _ := d.dev.List(); len(ds) != 1 || ds[0]["connected"] != true {
		t.Fatalf("%v", ds)
	}
	if found, err := d.dev.Revoke(id); !found || err != nil {
		t.Fatal(found, err)
	}
	if !c.closed() {
		t.Fatal("the connection of a revoked device stayed open")
	}
	// the token is now what a never issued one is
	if r := dialNet(t, d).ask(`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"token":"` + token + `"}}`); !reflect.DeepEqual(r["error"], unpaired) {
		t.Fatalf("%v", r)
	}
}

func TestARevokedConnectionThatStaysOpenIsRefusedAndEnds(t *testing.T) {
	d := startNet(t)
	token, id := pair(t, d, "phone")
	srvEnd, cli := net.Pipe()
	defer cli.Close()
	ended := make(chan struct{})
	go func() {
		d.net.rs.ServeNet(rpc.NewLineTransport(srvEnd), "10.0.0.9", func() {}) // a closer that closes nothing
		close(ended)
	}()
	r := bufio.NewReader(cli)
	say := func(line string) map[string]any {
		cli.SetDeadline(time.Now().Add(5 * time.Second))
		cli.Write([]byte(line + "\n"))
		b, err := r.ReadBytes('\n')
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		json.Unmarshal(b, &m)
		return m
	}
	if say(`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"token":"` + token + `"}}`)["result"] == nil {
		t.Fatal("hello")
	}
	d.dev.Revoke(id)
	if got := say(`{"jsonrpc":"2.0","id":2,"method":"whoami"}`); !reflect.DeepEqual(got["error"], unpaired) {
		t.Fatalf("%v", got)
	}
	select {
	case <-ended:
	case <-time.After(5 * time.Second):
		t.Fatal("the connection of a revoked device kept being served")
	}
}

func TestWrongCodesFromOneHostLockItOutWhateverPortItComesFrom(t *testing.T) {
	d := startNet(t)
	for i := 0; i < 5; i++ {
		c := dialNet(t, d) // a connection of its own each time: its source port differs
		if e := c.ask(`{"jsonrpc":"2.0","id":1,"method":"device.redeem","params":{"code":"WRONG","name":"p","request_id":"w` + string(rune('a'+i)) + `"}}`)["error"].(map[string]any); e["data"].(map[string]any)["gate"] != "pairing_code" {
			t.Fatalf("%v", e)
		}
	}
	code, _, _, _ := d.dev.Pair(time.Minute)
	e := dialNet(t, d).ask(`{"jsonrpc":"2.0","id":1,"method":"device.redeem","params":{"code":"` + code + `","name":"p","request_id":"late"}}`)["error"].(map[string]any)
	if e["data"].(map[string]any)["gate"] != "attempt_limit" {
		t.Fatalf("%v", e)
	}
}

func TestTheTokenIsNotKeptInAReply(t *testing.T) {
	d := startNet(t)
	token, _ := pair(t, d, "phone")
	var rows int
	d.db.QueryRow("SELECT count(*) FROM rpc_replies WHERE reply LIKE ?", "%"+token+"%").Scan(&rows)
	if rows != 0 {
		t.Fatal("the token was kept in a reply")
	}
}

func TestOnlyALanOrTailscaleAddressIsBound(t *testing.T) {
	for addr, ok := range map[string]bool{
		"127.0.0.1:0": true, "[::1]:0": true, "192.168.1.5:7000": true, "10.0.0.2:1": true, "172.16.3.4:1": true,
		"100.101.102.103:1": true, "[fd7a:115c:a1e0::1]:1": true, "[fe80::1]:1": true,
		"0.0.0.0:7000": false, ":7000": false, "[::]:7000": false, "8.8.8.8:7000": false, "100.128.0.1:1": false,
		"example.com:7000": false, "localhost:7000": false, "192.168.1.5": false,
	} {
		if err := checkBind(addr); (err == nil) != ok {
			t.Errorf("%s: %v", addr, err)
		}
	}
	if _, err := Start(Config{Home: t.TempDir(), DBFile: filepath.Join(t.TempDir(), "db"), Listen: "0.0.0.0:0"}); err == nil {
		t.Fatal("a daemon bound every interface")
	}
}

func TestTheCertificateOutlivesARestartAndNothingListensByDefault(t *testing.T) {
	dir := t.TempDir()
	cfg := Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Listen: "127.0.0.1:0"}
	d, err := Start(cfg)
	if err != nil {
		t.Fatal(err)
	}
	fp := d.NetworkFingerprint()
	d.Close()
	if st, err := os.Stat(filepath.Join(dir, "network.key")); err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("%v %v", st, err)
	}
	d, err = Start(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if d.NetworkFingerprint() != fp {
		t.Fatal("a restart changed the certificate a device pinned")
	}
	cfg.Listen = ""
	off, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db2.sqlite")})
	if err != nil {
		t.Fatal(err)
	}
	defer off.Close()
	if off.NetworkAddr() != "" || off.dev != nil {
		t.Fatal("a daemon with no listen address listens")
	}
}
