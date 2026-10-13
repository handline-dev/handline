package daemon

import (
	"bufio"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/authority"
	"github.com/handline/handline/go/internal/sessiontoken"
)

// startRelaying is a daemon serving a relay socket for a "gateway" that is this test process.
func startRelaying(t *testing.T, a authority.Authority, gatewayPid int) *Daemon {
	t.Helper()
	dir, err := os.MkdirTemp("", "hl")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	pidFile := filepath.Join(dir, "gateway.pid")
	if err := os.WriteFile(pidFile, []byte(strconv.Itoa(gatewayPid)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), SocketFile: filepath.Join(dir, "d.sock"),
		RelaySocket: filepath.Join(dir, "relay.sock"), PidFile: pidFile, Auth: a})
	if err != nil {
		if strings.Contains(err.Error(), "operation not permitted") {
			t.Skipf("this environment will not bind a socket: %v", err)
		}
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d
}

func dialRelay(t *testing.T, d *Daemon) *client {
	t.Helper()
	c, err := net.Dial("unix", d.cfg.RelaySocket)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	return &client{c, bufio.NewReader(c)}
}

const relayOpen = `{"jsonrpc":"2.0","id":0,"method":"relay.open","params":{"device_id":"dev-1","name":"Phone","role":"member"}}`

func TestARelayedCallFromAPairedDeviceIsAnsweredAtMemberWithOnlyThisDaemonRunning(t *testing.T) {
	d := startRelaying(t, fakeAuth{}, os.Getpid())
	c := dialRelay(t, d)
	if r := c.raw(t, relayOpen); r["result"] == nil {
		t.Fatalf("relay.open: %v", r)
	}
	// a hello changes nothing about who is calling
	r := c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"gateway","protocol":1,"key":"x","token":"t"}}`)
	res, _ := r["result"].(map[string]any)
	if res == nil || res["role"] != "member" {
		t.Fatalf("hello: %v", r)
	}
	if caller, _ := res["caller"].(map[string]any); caller["kind"] != "device" || caller["device"] != "dev-1" {
		t.Fatalf("the caller is not the device: %v", res["caller"])
	}
	if r := c.raw(t, `{"jsonrpc":"2.0","id":2,"method":"chat.messages","params":{}}`); r["error"] != nil {
		if e := r["error"].(map[string]any); strings.Contains(e["message"].(string), "this caller is") {
			t.Fatalf("a member call was refused for role: %v", r)
		}
	}
	r = c.raw(t, `{"jsonrpc":"2.0","id":3,"method":"settings.get","params":{}}`)
	if e, _ := r["error"].(map[string]any); e == nil || !strings.Contains(e["message"].(string), "this caller is member") {
		t.Fatalf("a founder call was not refused at the device's role: %v", r)
	}
}

func TestTheRelaySocketServesOnlyTheGateway(t *testing.T) {
	for name, tc := range map[string]struct {
		gateway int
		auth    fakeAuth
	}{
		"another process than the gateway": {os.Getppid(), fakeAuth{}},
		"a session of an agent":            {os.Getpid(), fakeAuth{agent: "worker"}},
		"sessions that cannot be read":     {os.Getpid(), fakeAuth{sessions: errors.New("permission denied")}},
	} {
		t.Run(name, func(t *testing.T) {
			d := startRelaying(t, tc.auth, tc.gateway)
			c := dialRelay(t, d)
			c.c.SetDeadline(time.Now().Add(5 * time.Second))
			c.c.Write([]byte(relayOpen + "\n"))
			if b, err := c.r.ReadBytes('\n'); err == nil {
				t.Fatalf("answered a peer that is not the gateway: %s", b)
			}
		})
	}
}

func TestARelayOpenThatIsNotAMemberDeviceIsClosedWithoutAByte(t *testing.T) {
	d := startRelaying(t, fakeAuth{}, os.Getpid())
	for _, open := range []string{
		`{"jsonrpc":"2.0","id":0,"method":"relay.open","params":{"device_id":"dev-1","name":"Phone","role":"founder"}}`,
		`{"jsonrpc":"2.0","id":0,"method":"hello","params":{"client":"x","protocol":1}}`,
		`{"jsonrpc":"2.0","id":0,"method":"relay.open","params":{"device_id":"../x","name":"Phone","role":"member"}}`,
	} {
		c := dialRelay(t, d)
		c.c.SetDeadline(time.Now().Add(5 * time.Second))
		c.c.Write([]byte(open + "\n"))
		if b, err := c.r.ReadBytes('\n'); err == nil {
			t.Fatalf("%s: answered: %s", open, b)
		}
	}
}

// The recorded sessions are how an agent's own process is told from the gateway. A directory that exists and cannot be
// read says nothing, and nothing is not "no agent".
func TestTheRelayRefusesWhenTheRecordedSessionsCannotBeRead(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads any directory")
	}
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	home := t.TempDir()
	store := authority.New(home)
	sessions := filepath.Join(store.Root, sessiontoken.NS)
	if err := os.MkdirAll(sessions, 0o700); err != nil {
		t.Fatal(err)
	}
	asked := func(d *Daemon) error {
		c := dialRelay(t, d)
		c.c.SetDeadline(time.Now().Add(5 * time.Second))
		c.c.Write([]byte(relayOpen + "\n"))
		_, err := c.r.ReadBytes('\n')
		return err
	}
	if err := asked(startRelaying(t, store, os.Getpid())); err != nil {
		t.Fatalf("readable sessions: %v", err)
	}
	if err := os.Chmod(sessions, 0); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(sessions, 0o700) })
	if err := asked(startRelaying(t, store, os.Getpid())); err == nil {
		t.Fatal("answered with the recorded sessions unreadable")
	}
	if err := os.RemoveAll(sessions); err != nil {
		t.Fatal(err)
	}
	if err := asked(startRelaying(t, store, os.Getpid())); err != nil {
		t.Fatalf("no sessions directory: %v", err)
	}
}
