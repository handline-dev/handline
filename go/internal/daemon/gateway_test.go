package daemon

import (
	"bufio"
	"bytes"
	"crypto/ecdsa"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/store"
)

// fakePython stands in for the Python daemon's relay socket: it hands the gateway one end of a pipe per dial and
// runs script on the other.
type fakePython struct {
	t      *testing.T
	dials  atomic.Int32
	down   atomic.Bool
	script func(conn net.Conn, open map[string]any)
	mu     sync.Mutex
	opens  []map[string]any
	calls  []string      // the methods it was sent after relay.open, in order
	closed chan struct{} // one value per relay connection that ended
}

func (f *fakePython) dial() (net.Conn, error) {
	f.dials.Add(1)
	if f.down.Load() {
		return nil, io.ErrClosedPipe
	}
	a, b := net.Pipe()
	go func() {
		defer func() { b.Close(); f.closed <- struct{}{} }()
		line, err := bufio.NewReader(b).ReadBytes('\n')
		if err != nil {
			return
		}
		var open map[string]any
		json.Unmarshal(line, &open)
		f.mu.Lock()
		f.opens = append(f.opens, open)
		f.mu.Unlock()
		b.Write([]byte(`{"jsonrpc":"2.0","id":0,"result":{"ok":true}}` + "\n"))
		f.script(&listening{Conn: b, f: f}, open)
	}()
	return a, nil
}

// listening records the method of every line read from it, as the relay's calls.
type listening struct {
	net.Conn
	f    *fakePython
	rest []byte
}

func (l *listening) Read(p []byte) (int, error) {
	n, err := l.Conn.Read(p)
	l.rest = append(l.rest, p[:n]...)
	for {
		i := bytes.IndexByte(l.rest, '\n')
		if i < 0 {
			break
		}
		var m struct{ Method string }
		json.Unmarshal(l.rest[:i], &m)
		l.rest = l.rest[i+1:]
		l.f.mu.Lock()
		l.f.calls = append(l.f.calls, m.Method)
		l.f.mu.Unlock()
	}
	return n, err
}

// onlyHelloReached fails unless the relay was sent a hello and nothing else.
func (f *fakePython) onlyHelloReached(t *testing.T) {
	t.Helper()
	f.mu.Lock()
	defer f.mu.Unlock()
	if !reflect.DeepEqual(f.calls, []string{"hello"}) {
		t.Fatalf("the relay was sent %v, want only the hello", f.calls)
	}
}

// echo answers every call with its method and the device it was opened for.
func echo(b net.Conn, open map[string]any) {
	r := bufio.NewReader(b)
	for {
		line, err := r.ReadBytes('\n')
		if err != nil {
			return
		}
		var m map[string]any
		json.Unmarshal(line, &m)
		if m["method"] == "hello" {
			b.Write([]byte(`{"jsonrpc":"2.0","id":` + string(mustJSON(m["id"])) + `,"result":{"capabilities":{"chat":true,"terminal_format":["text","bytes","styled"],"terminal_hosts":{"tmux":["text","bytes","styled"]}},"methods":[{"name":"chat.send","since":1,"role":"member","allowed":false}]}}` + "\n"))
			continue
		}
		if m["method"] == "stats.get" {
			b.Write([]byte(`{"jsonrpc":"2.0","method":"event","params":{"n":1}}` + "\n"))
		}
		if m["method"] == "agent.options" {
			b.Write([]byte(`{"jsonrpc":"2.0","id":` + string(mustJSON(m["id"])) + `,"error":{"code":-32001,"message":"not_found","data":{"code":"not_found"}}}` + "\n")) //nolint
			continue
		}
		if m["method"] == "session.type" { // hangs up mid-call
			return
		}
		b.Write([]byte(`{"jsonrpc":"2.0","id":` + string(mustJSON(m["id"])) + `,"result":{"method":"` + m["method"].(string) + `","device":"` + open["params"].(map[string]any)["device_id"].(string) + `"}}` + "\n"))
	}
}

func mustJSON(v any) []byte { b, _ := json.Marshal(v); return b }

func writeCert(t *testing.T, dir string) (crt, key string) {
	t.Helper()
	cert, err := ephemeralCert()
	if err != nil {
		t.Fatal(err)
	}
	der, _ := x509.MarshalECPrivateKey(cert.PrivateKey.(*ecdsa.PrivateKey))
	crt, key = filepath.Join(dir, "gw.crt"), filepath.Join(dir, "gw.key")
	os.WriteFile(crt, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Certificate[0]}), 0o644)
	os.WriteFile(key, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: der}), 0o600)
	return
}

// startGateway is a gateway over a database the "Python daemon" brought up, with a fake relay.
func startGateway(t *testing.T, web string, native ...string) (*Daemon, *fakePython, Config) {
	t.Helper()
	dir := t.TempDir()
	pre, err := store.Open(filepath.Join(dir, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	pre.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'a', 'seed', '{}')")
	pre.Close()
	crt, key := writeCert(t, dir)
	cfg := Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Listen: "127.0.0.1:0", CertFile: crt, KeyFile: key, Gateway: true,
		RelaySocket: filepath.Join(dir, "relay.sock"), PidFile: filepath.Join(dir, "protected", "gateway", "pid"), Native: native, WebDir: web, Auth: fakeAuth{}}
	d, err := Start(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	f := &fakePython{t: t, script: echo, closed: make(chan struct{}, 16)}
	d.net.rs.Relay.Dial = f.dial
	return d, f, cfg
}

func helloAs(t *testing.T, d *Daemon, token string) *wsClient {
	t.Helper()
	c := dialNet(t, d)
	if r := c.ask(`{"jsonrpc":"2.0","id":"h","method":"hello","params":{"client":"t","protocol":1,"token":"` + token + `"}}`); r["result"] == nil {
		t.Fatalf("%v", r)
	}
	return c
}

func TestAGatewayHelloReportsTheCapabilitiesAndMethodsTheDaemonServes(t *testing.T) {
	d, _, _ := startGateway(t, "")
	token, _ := pair(t, d, "phone")
	c := dialNet(t, d)
	r := c.ask(`{"jsonrpc":"2.0","id":"h","method":"hello","params":{"client":"t","protocol":1,"token":"` + token + `"}}`)
	res := r["result"].(map[string]any)
	caps := res["capabilities"].(map[string]any)
	if !reflect.DeepEqual(caps["terminal_format"], []any{"text", "bytes", "styled"}) || caps["terminal_hosts"] == nil || caps["chat"] != true {
		t.Fatalf("%v", caps)
	}
	var found map[string]any
	for _, m := range res["methods"].([]any) {
		if e := m.(map[string]any); e["name"] == "chat.send" {
			found = e
		}
	}
	if found == nil || found["allowed"] != false {
		t.Fatalf("the daemon says a device may not call chat.send; the hello says %v", found)
	}
}

func TestAGatewayHelloAsksTheDaemonOnTheConnectionTheDeviceUsesForItsCalls(t *testing.T) {
	d, f, _ := startGateway(t, "")
	token, id := pair(t, d, "phone")
	c := helloAs(t, d, token)
	if f.dials.Load() != 1 {
		t.Fatalf("%d relay connections for a hello", f.dials.Load())
	}
	if r := c.ask(`{"jsonrpc":"2.0","id":2,"method":"chat.send","params":{"request_id":"x"}}`); r["result"].(map[string]any)["device"] != id {
		t.Fatalf("%v", r)
	}
	if f.dials.Load() != 1 {
		t.Fatalf("%d relay connections for a hello and a call", f.dials.Load())
	}
}

func TestAGatewayHelloAnswersWithoutTheDaemonsFormatsWhenTheRelayIsDown(t *testing.T) {
	d, f, _ := startGateway(t, "")
	f.down.Store(true)
	token, _ := pair(t, d, "phone")
	r := dialNet(t, d).ask(`{"jsonrpc":"2.0","id":"h","method":"hello","params":{"client":"t","protocol":1,"token":"` + token + `"}}`)
	caps := r["result"].(map[string]any)["capabilities"].(map[string]any)
	if caps["chat"] != false || !reflect.DeepEqual(caps["terminal_format"], []any{}) {
		t.Fatalf("%v", caps)
	}
}

func (c *wsClient) read() map[string]any {
	c.t.Helper()
	c.ws.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, b, err := c.ws.ReadMessage()
	if err != nil {
		c.t.Fatal(err)
	}
	var m map[string]any
	json.Unmarshal(b, &m)
	return m
}

func (c *wsClient) send(msg string) { c.ws.WriteMessage(websocket.TextMessage, []byte(msg)) }

func gate(m map[string]any) any {
	if e, ok := m["error"].(map[string]any); ok {
		return e["data"].(map[string]any)["gate"]
	}
	return nil
}

func TestAGatewayRelaysWhatItDoesNotServeToTheRelayAsTheDevice(t *testing.T) {
	d, f, _ := startGateway(t, "")
	token, id := pair(t, d, "phone")
	c := helloAs(t, d, token)
	r := c.ask(`{"jsonrpc":"2.0","id":7,"method":"chat.send","params":{"request_id":"x"}}`)
	if r["id"] != float64(7) || !reflect.DeepEqual(r["result"], map[string]any{"method": "chat.send", "device": id}) {
		t.Fatalf("%v", r)
	}
	open := f.opens[0]["params"].(map[string]any)
	if open["device_id"] != id || open["role"] != "member" || open["name"] != "phone" || f.opens[0]["method"] != "relay.open" {
		t.Fatalf("%v", f.opens[0])
	}
	// a notification comes back on the device's connection, then its reply, and an error comes back whole
	c.send(`{"jsonrpc":"2.0","id":8,"method":"stats.get"}`)
	if n := c.read(); n["method"] != "event" || n["id"] != nil {
		t.Fatalf("%v", n)
	}
	if r := c.read(); r["id"] != float64(8) || r["result"] == nil {
		t.Fatalf("%v", r)
	}
	e := c.ask(`{"jsonrpc":"2.0","id":"s","method":"agent.options"}`)
	if e["id"] != "s" || e["error"].(map[string]any)["code"] != float64(-32001) {
		t.Fatalf("%v", e)
	}
	if f.dials.Load() != 1 {
		t.Fatalf("%d relay connections for one device connection", f.dials.Load())
	}
	// a second connection of the device has a relay connection of its own
	helloAs(t, d, token).ask(`{"jsonrpc":"2.0","id":1,"method":"chat.send","params":{"request_id":"y"}}`)
	if f.dials.Load() != 2 {
		t.Fatal(f.dials.Load())
	}
}

func TestACallAboveAMemberOrOutsideTheContractNeverReachesTheRelay(t *testing.T) {
	d, f, _ := startGateway(t, "")
	c := helloAs(t, d, mustPair(t, d))
	for _, call := range []string{"account.remove", "account.login", "settings.set", "hook.record", "task.progress"} {
		r := c.ask(`{"jsonrpc":"2.0","id":1,"method":"` + call + `","params":{"request_id":"x"}}`)
		if gate(r) != "role" {
			t.Fatalf("%s: %v", call, r)
		}
	}
	for _, call := range []string{"no.such.method", "", "Chat.send", "relay.open"} {
		r := c.ask(`{"jsonrpc":"2.0","id":1,"method":"` + call + `"}`)
		if e, _ := r["error"].(map[string]any); e == nil || e["code"] != float64(-32601) {
			t.Fatalf("%q: %v", call, r)
		}
	}
	f.onlyHelloReached(t)
}

func mustPair(t *testing.T, d *Daemon) string { token, _ := pair(t, d, "phone"); return token }

func TestAnUnpairedConnectionGetsNothingForARelayedMethodEither(t *testing.T) {
	d, f, _ := startGateway(t, "")
	c := dialNet(t, d)
	for _, call := range []string{"chat.send", "stats.get", "terminal.attach", "subscribe", "events.feed", "tasks.list", "account.remove"} {
		if r := c.ask(`{"jsonrpc":"2.0","id":1,"method":"` + call + `","params":{"request_id":"x"}}`); !reflect.DeepEqual(r["error"], unpaired) {
			t.Fatalf("%s: %v", call, r)
		}
	}
	if f.dials.Load() != 0 {
		t.Fatal("an unpaired call reached the relay")
	}
}

func TestADroppedRelayAnswersTheCallInFlightAndTheNextCallOpensAgain(t *testing.T) {
	d, f, _ := startGateway(t, "")
	c := helloAs(t, d, mustPair(t, d))
	r := c.ask(`{"jsonrpc":"2.0","id":5,"method":"session.type","params":{"request_id":"x"}}`)
	e, _ := r["error"].(map[string]any)
	if r["id"] != float64(5) || e == nil || e["code"] != float64(-32004) {
		t.Fatalf("%v", r)
	}
	if r := c.ask(`{"jsonrpc":"2.0","id":6,"method":"chat.send","params":{"request_id":"y"}}`); r["result"] == nil {
		t.Fatalf("%v", r)
	}
	if f.dials.Load() != 2 {
		t.Fatal(f.dials.Load())
	}
}

func TestARelayThatIsDownFailsTheCallAtOnce(t *testing.T) {
	d, f, _ := startGateway(t, "", "daemon.status")
	f.down.Store(true)
	c := helloAs(t, d, mustPair(t, d))
	start := time.Now()
	r := c.ask(`{"jsonrpc":"2.0","id":1,"method":"chat.send","params":{"request_id":"x"}}`)
	if e, _ := r["error"].(map[string]any); e == nil || e["code"] != float64(-32004) || time.Since(start) > 2*time.Second {
		t.Fatalf("%v after %v", r, time.Since(start))
	}
	// what the gateway serves itself still answers
	if c.ask(`{"jsonrpc":"2.0","id":2,"method":"daemon.status"}`)["result"] == nil {
		t.Fatal("daemon.status")
	}
}

func TestRevokingADeviceEndsItsRelayConnectionAtOnce(t *testing.T) {
	d, f, _ := startGateway(t, "")
	token, id := pair(t, d, "phone")
	c := helloAs(t, d, token)
	c.ask(`{"jsonrpc":"2.0","id":1,"method":"chat.send","params":{"request_id":"x"}}`)
	d.dev.Revoke(id)
	select {
	case <-f.closed:
	case <-time.After(5 * time.Second):
		t.Fatal("the relay was not told the device was gone")
	}
	if !c.closed() {
		t.Fatal("the device connection stayed open")
	}
}

func TestADeviceThatHangsUpEndsItsRelayConnection(t *testing.T) {
	d, f, _ := startGateway(t, "")
	c := helloAs(t, d, mustPair(t, d))
	c.ask(`{"jsonrpc":"2.0","id":1,"method":"chat.send","params":{"request_id":"x"}}`)
	c.ws.Close()
	select {
	case <-f.closed:
	case <-time.After(5 * time.Second):
		t.Fatal("the relay was not told the device was gone")
	}
}

func TestOnlyWhatIsNamedNativeIsServedHere(t *testing.T) {
	d, f, _ := startGateway(t, "")
	r := helloAs(t, d, mustPair(t, d)).ask(`{"jsonrpc":"2.0","id":1,"method":"daemon.status"}`)
	if f.dials.Load() != 1 || r["result"].(map[string]any)["method"] != "daemon.status" {
		t.Fatalf("a read the config does not name is relayed: %v", r)
	}
	d2, f2, _ := startGateway(t, "", "daemon.status")
	if r := helloAs(t, d2, mustPair(t, d2)).ask(`{"jsonrpc":"2.0","id":1,"method":"daemon.status"}`); r["result"] == nil || r["result"].(map[string]any)["method"] != nil {
		t.Fatalf("a named read is served here: %v", r)
	}
	f2.onlyHelloReached(t)
}

func dump(t *testing.T, db interface {
	Query(string, ...any) (*sql.Rows, error)
}, skip string) string {
	t.Helper()
	var out strings.Builder
	tables, err := db.Query("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for tables.Next() {
		var n string
		tables.Scan(&n)
		names = append(names, n)
	}
	tables.Close()
	for _, n := range names {
		if n == skip {
			continue
		}
		rows, err := db.Query("SELECT * FROM " + n)
		if err != nil {
			t.Fatal(err)
		}
		cols, _ := rows.Columns()
		for rows.Next() {
			vals := make([]any, len(cols))
			ptrs := make([]any, len(cols))
			for i := range vals {
				ptrs[i] = &vals[i]
			}
			rows.Scan(ptrs...)
			out.WriteString(n + fmt.Sprint(vals) + "\n")
		}
		rows.Close()
	}
	return out.String()
}

func TestAGatewayWritesNothingButItsDevicesAndItsPidFile(t *testing.T) {
	d, _, cfg := startGateway(t, "", "daemon.status", "tasks.list")
	before := dump(t, d.db, "devices")
	spool := filepath.Join(cfg.Home, "hooks-spool")
	os.MkdirAll(spool, 0o700)
	os.WriteFile(filepath.Join(spool, "a.jsonl"), []byte(`{"request_id":"r1"}`+"\n"), 0o600)
	files := func() string {
		var all []string
		filepath.Walk(cfg.Home, func(p string, i os.FileInfo, err error) error {
			if err == nil && !i.IsDir() && !strings.Contains(p, "db.sqlite") {
				all = append(all, strings.TrimPrefix(p, cfg.Home)+fmt.Sprint(i.Size()))
			}
			return nil
		})
		sort.Strings(all)
		return strings.Join(all, ",")
	}
	filesBefore := files()
	token, id := pair(t, d, "phone")
	c := helloAs(t, d, token)
	c.ask(`{"jsonrpc":"2.0","id":1,"method":"daemon.status"}`)
	c.ask(`{"jsonrpc":"2.0","id":2,"method":"tasks.list"}`)
	c.ask(`{"jsonrpc":"2.0","id":3,"method":"hook.record","params":{"request_id":"z"}}`)
	d.dev.Revoke(id)
	time.Sleep(3 * hooks.SpoolEvery / 2) // a spool loop would have run
	if after := dump(t, d.db, "devices"); after != before {
		t.Fatalf("a gateway wrote to the database:\nbefore %s\nafter %s", before, after)
	}
	if files() != filesBefore {
		t.Fatalf("files changed: %s -> %s", filesBefore, files())
	}
	if _, err := os.Stat(filepath.Join(spool, "a.jsonl")); err != nil {
		t.Fatal("the spool was read")
	}
	if d.hooks != nil {
		t.Fatal("a gateway has a hook recorder")
	}
}

func TestAGatewayNeedsTheDatabaseTheOtherDaemonBroughtUp(t *testing.T) {
	dir := t.TempDir()
	crt, key := writeCert(t, dir)
	cfg := Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Listen: "127.0.0.1:0", CertFile: crt, KeyFile: key, Gateway: true,
		RelaySocket: filepath.Join(dir, "relay.sock"), PidFile: filepath.Join(dir, "gateway.pid")}
	if _, err := Start(cfg); err == nil || !errors.Is(err, store.ErrSchema) {
		t.Fatalf("no database: %v", err)
	}
	if _, err := os.Stat(cfg.DBFile); err == nil {
		t.Fatal("a gateway made the database")
	}
	os.WriteFile(cfg.DBFile, nil, 0o600) // an empty file is a database at version 0
	if _, err := Start(cfg); err == nil || !errors.Is(err, store.ErrSchema) {
		t.Fatalf("an old database: %v", err)
	}
	if st, _ := os.Stat(cfg.DBFile); st.Size() != 0 {
		t.Fatal("a gateway migrated a database")
	}
	if _, err := os.Stat(cfg.PidFile); err == nil {
		t.Fatal("a pid file outlived a start that failed")
	}
}

func TestAGatewayRefusesAConfigThatWouldMakeItSomethingElse(t *testing.T) {
	_, _, cfg := startGateway(t, "")
	for name, mod := range map[string]func(*Config){
		"a certificate without its key": func(c *Config) { c.KeyFile = "" },
		"no listen":                     func(c *Config) { c.Listen = "" },
		"the python socket":             func(c *Config) { c.SocketFile = filepath.Join(c.Home, "daemon.sock") },
		"no relay":                      func(c *Config) { c.RelaySocket = "" },
		"all interfaces":                func(c *Config) { c.Listen = "0.0.0.0:0" },
	} {
		c := cfg
		c.PidFile = filepath.Join(t.TempDir(), "p.pid")
		mod(&c)
		if d, err := Start(c); err == nil {
			d.Close()
			t.Errorf("%s: started", name)
		}
	}
}

func TestThePidFileIsPrivateNamesTheGatewayAndGoesWithIt(t *testing.T) {
	d, _, cfg := startGateway(t, "")
	if st, err := os.Stat(filepath.Dir(cfg.PidFile)); err != nil || st.Mode().Perm() != 0o700 {
		t.Fatalf("the pid file's directory: %v %v", st, err)
	}
	st, err := os.Stat(cfg.PidFile)
	b, _ := os.ReadFile(cfg.PidFile)
	if err != nil || st.Mode().Perm() != 0o600 || strings.TrimSpace(string(b)) != fmt.Sprint(os.Getpid()) || !strings.HasSuffix(string(b), "\n") {
		t.Fatalf("%v %v %q", st, err, b)
	}
	// another gateway that is alive holds it
	os.WriteFile(cfg.PidFile, []byte(fmt.Sprint(os.Getppid())+"\n"), 0o600)
	c := cfg
	c.DBFile = cfg.DBFile
	if d2, err := Start(c); err == nil {
		d2.Close()
		t.Fatal("two gateways")
	}
	os.WriteFile(cfg.PidFile, []byte(fmt.Sprint(os.Getpid())+"\n"), 0o600)
	d.Close()
	if _, err := os.Stat(cfg.PidFile); err == nil {
		t.Fatal("the pid file stayed")
	}
}

func TestARenewedCertificateIsServedWithoutARestart(t *testing.T) {
	d, _, cfg := startGateway(t, "")
	fp := d.NetworkFingerprint()
	crt, key := writeCert(t, t.TempDir())
	newCrt, _ := os.ReadFile(crt)
	newKey, _ := os.ReadFile(key)
	os.WriteFile(cfg.CertFile, newCrt, 0o644)
	os.WriteFile(cfg.KeyFile, newKey, 0o600)
	future := time.Now().Add(time.Minute)
	os.Chtimes(cfg.CertFile, future, future)
	os.Chtimes(cfg.KeyFile, future, future)
	if got := d.NetworkFingerprint(); got == fp || got == "" {
		t.Fatalf("%s -> %s", fp, got)
	}
	conn, err := tls.Dial("tcp", d.NetworkAddr(), &tls.Config{InsecureSkipVerify: true})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	sum := sha256.Sum256(conn.ConnectionState().PeerCertificates[0].Raw)
	if hex.EncodeToString(sum[:]) != d.NetworkFingerprint() {
		t.Fatal("the listener still presents the old certificate")
	}
	os.WriteFile(cfg.CertFile, []byte("half"), 0o644) // a renewal caught half written
	os.Chtimes(cfg.CertFile, future.Add(time.Minute), future.Add(time.Minute))
	if d.NetworkFingerprint() == "" {
		t.Fatal("a half-written certificate took the listener down")
	}
}

func TestThePairingCodeNamesTheAdvertisedAddressAndTheFingerprint(t *testing.T) {
	dir := t.TempDir()
	pre, _ := store.Open(filepath.Join(dir, "db.sqlite"))
	pre.Close()
	crt, key := writeCert(t, dir)
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Listen: "127.0.0.1:0", CertFile: crt, KeyFile: key,
		Advertise: "mac.tail1234.ts.net:7443", Auth: fakeAuth{}})
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	code, _, fp, uri := d.dev.Pair(time.Minute)
	u, _ := url.Parse(uri)
	q := u.Query()
	if q.Get("host") != "mac.tail1234.ts.net:7443" || q.Get("fp") != d.NetworkFingerprint() || fp != q.Get("fp") || q.Get("code") != code {
		t.Fatal(uri)
	}
}

func get(t *testing.T, d *Daemon, method, path string) *http.Response {
	t.Helper()
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}
	req, _ := http.NewRequest(method, "https://"+d.NetworkAddr()+"/", nil)
	req.URL.Opaque = "//" + d.NetworkAddr() + path // sent as it is, so ".." and %2e survive
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func TestTheWebUIIsServedOnTheListenersOriginAndOnlyWhatIsInsideIt(t *testing.T) {
	web := filepath.Join(t.TempDir(), "web")
	os.MkdirAll(filepath.Join(web, "assets", "sub"), 0o755)
	os.WriteFile(filepath.Join(web, "index.html"), []byte("<html>app</html>"), 0o644)
	os.WriteFile(filepath.Join(web, "assets", "app.js"), []byte("1"), 0o644)
	os.WriteFile(filepath.Join(web, "assets", "app.css"), []byte("1"), 0o644)
	os.WriteFile(filepath.Join(web, "assets", "x.wasm"), []byte("1"), 0o644)
	os.WriteFile(filepath.Join(web, "assets", "sub", "index.html"), []byte("sub"), 0o644)
	os.WriteFile(filepath.Join(web, ".env"), []byte("secret"), 0o644)
	os.MkdirAll(filepath.Join(web, "empty"), 0o755)
	secret := filepath.Join(filepath.Dir(web), "secret.txt")
	os.WriteFile(secret, []byte("outside"), 0o644)
	os.Symlink(secret, filepath.Join(web, "link.txt"))
	d, _, _ := startGateway(t, web)
	body := func(r *http.Response) string { b, _ := io.ReadAll(r.Body); return string(b) }

	r := get(t, d, "GET", "/")
	if r.StatusCode != 200 || body(r) != "<html>app</html>" || r.Header.Get("Cache-Control") != "no-store" || r.Header.Get("Last-Modified") != "" ||
		!strings.HasPrefix(r.Header.Get("Content-Type"), "text/html") || r.Header.Get("X-Content-Type-Options") != "nosniff" {
		t.Fatalf("index: %v", r.Header)
	}
	if r := get(t, d, "GET", "/index.html"); r.Header.Get("Cache-Control") != "no-store" {
		t.Fatal("index.html is cached")
	}
	for path, ct := range map[string]string{"/assets/app.js": "text/javascript", "/assets/app.css": "text/css", "/assets/x.wasm": "application/wasm"} {
		if r := get(t, d, "GET", path); r.StatusCode != 200 || !strings.HasPrefix(r.Header.Get("Content-Type"), ct) {
			t.Errorf("%s: %d %s", path, r.StatusCode, r.Header.Get("Content-Type"))
		}
	}
	if r := get(t, d, "GET", "/assets/sub/"); body(r) != "sub" {
		t.Fatal("a directory with an index serves it")
	}
	for _, path := range []string{"/missing.js", "/empty/", "/assets/", "/.env", "/assets/../.env", "/../secret.txt", "/%2e%2e/secret.txt",
		"/assets/%2e%2e/%2e%2e/secret.txt", "/link.txt", "/%2e%2e%2fsecret.txt", "/assets/..%2f..%2fsecret.txt", "//etc/passwd"} {
		r := get(t, d, "GET", path)
		if r.StatusCode == 200 && strings.Contains(body(r), "outside") || r.StatusCode == 200 && strings.Contains(body(r), "secret") {
			t.Errorf("%s leaked", path)
		}
		if r.StatusCode == 200 {
			t.Errorf("%s: %d", path, r.StatusCode)
		}
	}
	if r := get(t, d, "GET", "/assets/"); r.StatusCode != 404 {
		t.Fatalf("a listing: %d", r.StatusCode)
	}
	if r := get(t, d, "POST", "/"); r.StatusCode != 405 {
		t.Fatal(r.StatusCode)
	}
	if r := get(t, d, "GET", "/?token=abc"); r.StatusCode != 400 {
		t.Fatal("a token in the URL was accepted")
	}
}

func TestNothingIsServedWithoutAWebDirectoryAndASocketFromAnotherPageIsRefused(t *testing.T) {
	d, _, _ := startGateway(t, "")
	if r := get(t, d, "GET", "/"); r.StatusCode != 404 {
		t.Fatal(r.StatusCode)
	}
	dialer := websocket.Dialer{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}
	for origin, ok := range map[string]bool{"https://" + d.NetworkAddr(): true, "https://evil.example": false, "null": false} {
		ws, _, err := dialer.Dial("wss://"+d.NetworkAddr()+"/rpc", http.Header{"Origin": {origin}})
		if (err == nil) != ok {
			t.Errorf("origin %s: %v", origin, err)
		}
		if ws != nil {
			ws.Close()
		}
	}
}

func TestTheRelaySocketIsTrustedOnlyWhenTheDaemonHoldingTheLockAnswersOnIt(t *testing.T) {
	c, _ := net.Pipe()
	defer c.Close()
	asPid := func(pid int) func(net.Conn) int { return func(net.Conn) int { return pid } }
	real := peer.Started
	lock := filepath.Join(t.TempDir(), "daemon.lock")
	for name, body := range map[string]string{"missing": "", "garbage": "x\n", "another process": fmt.Sprintf("%-16d\n", os.Getppid()), "zero": "0"} {
		if body != "" {
			os.WriteFile(lock, []byte(body), 0o600)
		}
		if err := relayPeerIsTheDaemon(c, lock, asPid(os.Getpid()), real); err == nil {
			t.Errorf("%s: trusted", name)
		}
	}
	os.WriteFile(lock, []byte(fmt.Sprintf("%-16d\n", os.Getpid())), 0o600)
	if err := relayPeerIsTheDaemon(c, lock, asPid(os.Getpid()), real); err != nil {
		t.Fatal(err)
	}
	if err := relayPeerIsTheDaemon(c, lock, asPid(os.Getpid()+1), real); err == nil {
		t.Fatal("a socket answered by another process was trusted")
	}
	if err := relayPeerIsTheDaemon(c, lock, asPid(0), real); err == nil {
		t.Fatal("a peer the kernel could not name was trusted")
	}
	// a pid taken after the lock was written (a crashed daemon's pid, reused) is not the daemon
	old := time.Now().Add(-time.Hour)
	os.Chtimes(lock, old, old)
	if err := relayPeerIsTheDaemon(c, lock, asPid(os.Getpid()), real); err == nil {
		t.Fatal("a process that started after the lock was written was trusted")
	}
	now := time.Now()
	os.Chtimes(lock, now, now)
	if err := relayPeerIsTheDaemon(c, lock, asPid(os.Getpid()), func(int) (time.Time, bool) { return time.Time{}, false }); err == nil {
		t.Fatal("a process whose start time is unknown was trusted")
	}
}

// startPlain is a gateway with no certificate (Tailscale mode) on loopback, where a test can reach it.
func startPlain(t *testing.T, web string) *Daemon {
	t.Helper()
	_, _, cfg := startGateway(t, web)
	cfg.CertFile, cfg.KeyFile = "", ""
	cfg.PidFile = filepath.Join(t.TempDir(), "p.pid")
	d, err := Start(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d
}

func TestWithoutACertificateTheGatewayServesPlainHTTPAndWebSocketWithNothingToPin(t *testing.T) {
	web := t.TempDir()
	os.WriteFile(filepath.Join(web, "index.html"), []byte("<html>pair</html>"), 0o644)
	d := startPlain(t, web)
	if d.NetworkFingerprint() != "" {
		t.Fatal("a plain listener has no certificate to fingerprint")
	}
	if resp, err := http.Get("http://" + d.NetworkAddr() + "/"); err != nil || resp.StatusCode != 200 {
		t.Fatalf("%v %v", resp, err)
	}
	if _, err := (&http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}).Get("https://" + d.NetworkAddr() + "/"); err == nil {
		t.Fatal("TLS answered on a plain listener")
	}
	for origin, ok := range map[string]bool{"http://" + d.NetworkAddr(): true, "http://evil.example": false, "https://" + d.NetworkAddr(): true} {
		ws, _, err := websocket.DefaultDialer.Dial("ws://"+d.NetworkAddr()+"/rpc", http.Header{"Origin": {origin}})
		if (err == nil) != ok {
			t.Errorf("origin %s: %v", origin, err)
		}
		if ws != nil {
			ws.Close()
		}
	}
	ws, _, err := websocket.DefaultDialer.Dial("ws://"+d.NetworkAddr()+"/rpc", nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	c := &wsClient{t, ws}
	if r := c.ask(`{"jsonrpc":"2.0","id":1,"method":"agents.list"}`); gate(r) != "unpaired" {
		t.Fatalf("%v", r)
	}
	_, _, _, uri := d.dev.Pair(time.Minute)
	if u, _ := url.Parse(uri); u.Query().Has("fp") || u.Query().Get("host") != d.NetworkAddr() {
		t.Fatal(uri)
	}
}

func fakeInterfaces(t *testing.T, as ...ifaceAddr) {
	was := interfaceAddrs
	t.Cleanup(func() { interfaceAddrs = was })
	interfaceAddrs = func() ([]ifaceAddr, error) { return as, nil }
}

func ia(name, ip string) ifaceAddr { return ifaceAddr{name, net.ParseIP(ip)} }

func TestThePlainListenerRefusesEveryAddressButTheTailscaleInterfaceAndLoopback(t *testing.T) {
	// 100.64.9.9 on en0 is carrier-grade NAT on an ordinary interface: in the range, not Tailscale.
	fakeInterfaces(t, ia("utun4", "100.64.0.1"), ia("tailscale0", "100.127.255.254"), ia("en0", "100.64.9.9"), ia("en0", "192.168.1.5"))
	for addr, ok := range map[string]bool{"100.64.0.1:1": true, "100.127.255.254:1": true, "127.0.0.1:1": true, "[::1]:1": true,
		"100.64.9.9:1": false, "192.168.1.5:1": false, "10.0.0.2:1": false, "172.16.0.1:1": false, "169.254.1.1:1": false,
		"100.128.0.1:1": false, "100.63.255.255:1": false, "100.64.0.2:1": false, "0.0.0.0:1": false, "8.8.8.8:1": false, "host.ts.net:1": false} {
		if err := checkPlainBind(addr); (err == nil) != ok {
			t.Errorf("%s: %v", addr, err)
		}
	}
	// and Start refuses before it binds anything
	_, _, cfg := startGateway(t, "")
	cfg.CertFile, cfg.KeyFile = "", ""
	for _, addr := range []string{"192.168.1.5:0", "10.0.0.2:0", "0.0.0.0:0"} {
		c := cfg
		c.Listen, c.PidFile = addr, filepath.Join(t.TempDir(), "p.pid")
		d, err := Start(c) // the address may not be this machine's, so the refusal must be the plain-HTTP rule, not a failed bind
		if err == nil {
			d.Close()
			t.Errorf("%s: plain listener started", addr)
		} else if !strings.Contains(err.Error(), "plain HTTP") && !strings.Contains(err.Error(), "binds every interface") {
			t.Errorf("%s: %v", addr, err)
		}
	}
}

func TestTailscaleModeBindsTheTailscaleInterfaceAndNeverFallsBack(t *testing.T) {
	fakeInterfaces(t, ia("en0", "100.64.9.9"), ia("en1", "192.168.1.5"), ia("utun3", "100.101.102.103"))
	if a, err := plainAddr(":7443"); err != nil || a != "100.101.102.103:7443" {
		t.Fatalf("%q %v", a, err)
	}
	if a, err := plainAddr("127.0.0.1:7443"); err != nil || a != "127.0.0.1:7443" {
		t.Fatalf("%q %v", a, err)
	}
	// the only 100.64/10 address is on a LAN interface: no Tailscale, no fallback to it
	fakeInterfaces(t, ia("en0", "100.64.9.9"), ia("en1", "192.168.1.5"))
	if a, err := plainAddr(":7443"); err == nil {
		t.Fatalf("fell back to %q", a)
	}
	_, _, cfg := startGateway(t, "")
	cfg.CertFile, cfg.KeyFile, cfg.Listen, cfg.PidFile = "", "", ":0", filepath.Join(t.TempDir(), "p.pid")
	if d, err := Start(cfg); err == nil {
		d.Close()
		t.Fatal("started with no Tailscale address")
	}
}
