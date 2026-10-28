package daemon

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func founder(t *testing.T, d *Daemon) *client {
	t.Helper()
	c := dial(t, d)
	c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"k"}}`)
	return c
}

// stubVersion makes every `exe --version` print out, or fail with err.
func stubVersion(t *testing.T, out string, err error) {
	t.Helper()
	old := versionOf
	versionOf = func(string) (string, error) { return out, err }
	t.Cleanup(func() { versionOf = old })
}

func upgradeCall(exe, sha string) string {
	return fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"daemon.upgrade","params":{"exe":%q,"sha":%q,"request_id":"up-%s-%s"}}`, exe, sha, filepath.Base(exe), sha)
}

func TestDaemonUpgradeRefusesABinaryThatIsNotTheNamedBuild(t *testing.T) {
	for _, tc := range []struct {
		name, exe, sha, prints string
		fails                  error
		code                   string
	}{
		{"relative exe", "bin/handlined", "0123456789ab", "0123456789ab", nil, "bad_request"},
		{"sha not hex", "/x/handlined", "main", "main", nil, "bad_request"},
		{"does not start", "/x/handlined", "0123456789ab", "0123456789ab", errors.New("exit status 2"), "refused"},
		{"another build", "/x/handlined", "0123456789ab", "ba9876543210", nil, "refused"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			stubVersion(t, tc.prints, tc.fails)
			d := start(t, fakeAuth{key: "k"}, "")
			r := founder(t, d).raw(t, upgradeCall(tc.exe, tc.sha))
			if _, code := errOf(t, r); code != tc.code {
				t.Fatalf("%s: %v", code, r)
			}
			select {
			case <-d.Upgrading():
				t.Fatal("a refused upgrade stopped the daemon")
			default:
			}
		})
	}
}

func TestDaemonUpgradeRepliesThenExecsTheBinaryWithTheSameArguments(t *testing.T) {
	for _, prints := range []string{"0123456789ab", "1.4.0+0123456789ab-dirty"} {
		t.Run(prints, func(t *testing.T) {
			stubVersion(t, prints, nil)
			var argv []string
			old := execve
			execve = func(exe string, args, _ []string) error { argv = append([]string{exe}, args...); return nil }
			t.Cleanup(func() { execve = old })
			d := start(t, fakeAuth{key: "k"}, "")
			if err := d.ExecUpgrade(); err != nil || argv != nil {
				t.Fatalf("exec with no upgrade asked: %v %v", err, argv)
			}
			r := founder(t, d).raw(t, upgradeCall("/new/handlined", "0123456789ab"))
			res, _ := r["result"].(map[string]any)
			if res["to"] != "0123456789ab" || res["exe"] != "/new/handlined" {
				t.Fatalf("%v", r)
			}
			select {
			case <-d.Upgrading():
			case <-time.After(5 * time.Second):
				t.Fatal("the daemon was not told to stop after the reply")
			}
			if err := d.ExecUpgrade(); err != nil {
				t.Fatal(err)
			}
			want := append([]string{"/new/handlined", "/new/handlined"}, os.Args[1:]...)
			if strings.Join(argv, " ") != strings.Join(want, " ") {
				t.Fatalf("exec %v, want %v", argv, want)
			}
		})
	}
}

func TestAnAgentMayNotUpgradeTheDaemon(t *testing.T) {
	stubVersion(t, "0123456789ab", nil)
	d := start(t, fakeAuth{agent: "worker"}, "")
	c := dial(t, d)
	c.raw(t, hello)
	if _, code := errOf(t, c.raw(t, upgradeCall("/new/handlined", "0123456789ab"))); code != "refused" {
		t.Fatal(code)
	}
}

// hello says which build and binary answer, and a proven client of another build is logged once on its connection, not at each hello;
// an anonymous one is not logged at all.
func TestHelloCarriesVersionAndExeAndLogsAClientOfAnotherBuildOnce(t *testing.T) {
	dir := homeDir(t)
	var mu sync.Mutex
	var lines []string
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), SocketFile: filepath.Join(dir, "d.sock"), Auth: fakeAuth{key: "k"},
		Log: func(f string, a ...any) { mu.Lock(); lines = append(lines, fmt.Sprintf(f, a...)); mu.Unlock() }})
	if err != nil {
		t.Skipf("no socket here: %v", err)
	}
	t.Cleanup(d.Close)
	exe, _ := os.Executable()
	c := dial(t, d)
	for i := 0; i < 2; i++ {
		res, _ := c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"app","protocol":1,"key":"k","client_build":"feedfacecafe"}}`)["result"].(map[string]any)
		if res["version"] != d.local.Sha || res["exe"] != exe {
			t.Fatalf("%v", res)
		}
	}
	dial(t, d).raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"app","protocol":1,"key":"k","client_build":"`+d.local.Sha+`"}}`)
	dial(t, d).raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"stranger","protocol":1,"client_build":"0ddba11"}}`)
	mu.Lock()
	defer mu.Unlock()
	n := 0
	for _, l := range lines {
		if strings.Contains(l, `is build "feedfacecafe"`) {
			n++
		}
		if strings.Contains(l, d.local.Sha+`"`) || strings.Contains(l, "stranger") {
			t.Fatalf("a client of the same build, or an anonymous one, was logged: %q", l)
		}
	}
	if n != 1 {
		t.Fatalf("%d lines for one connection: %q", n, lines)
	}
}

// Only the founder's own socket may choose the binary: not the network listener, not the relay a gateway calls, not a gateway's socket.
func TestDaemonUpgradeIsServedOnTheLocalSocketOnly(t *testing.T) {
	d := start(t, fakeAuth{key: "k"}, "")
	if !d.local.Has("daemon.upgrade") {
		t.Fatal("the local socket does not serve daemon.upgrade")
	}
	for _, transport := range []string{"network", "relay"} {
		if d.newServer(transport).Has("daemon.upgrade") {
			t.Fatalf("the %s server serves daemon.upgrade", transport)
		}
	}
	if g, _, _ := startGateway(t, ""); g.local.Has("daemon.upgrade") {
		t.Fatal("a gateway serves daemon.upgrade")
	}
}

// A retry of an answered upgrade, as the new process gets it when the client reconnects, is the kept reply: the binary is not checked
// again and the daemon is not told to restart again.
func TestARetriedUpgradeGetsTheKeptReplyFromTheDatabase(t *testing.T) {
	var checks int
	old := versionOf
	versionOf = func(string) (string, error) { checks++; return "0123456789ab", nil }
	t.Cleanup(func() { versionOf = old })
	dir := homeDir(t)
	cfg := Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), SocketFile: filepath.Join(dir, "d.sock"), Auth: fakeAuth{key: "k"}}
	first, err := Start(cfg)
	if err != nil {
		t.Skipf("no socket here: %v", err)
	}
	want := founder(t, first).raw(t, upgradeCall("/new/handlined", "0123456789ab"))["result"]
	<-first.Upgrading()
	first.Close()
	second, err := Start(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(second.Close)
	got, _ := founder(t, second).raw(t, upgradeCall("/new/handlined", "0123456789ab"))["result"].(map[string]any)
	replayed := got["replayed"]
	delete(got, "replayed")
	if checks != 1 || replayed != true || fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("%d checks; retry %v, first %v", checks, got, want)
	}
	select {
	case <-second.Upgrading():
		t.Fatal("the retry told the new daemon to restart again")
	case <-time.After(200 * time.Millisecond):
	}
}
