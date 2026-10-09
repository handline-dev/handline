package weblogin

import (
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

const fakeURL = "https://claude.example/oauth/authorize?state=abc"

// rig is a home with accounts main and other, and a Manager whose login command is the script a test gives.
type rig struct {
	t     *testing.T
	home  string
	m     *Manager
	timer *timers
	mu    sync.Mutex
	moved []string
}

// timers hands out timer channels a test fires by duration.
type timers struct {
	mu   sync.Mutex
	chs  map[time.Duration]chan time.Time
	asks []time.Duration
}

func (ts *timers) wait(d time.Duration) <-chan time.Time {
	ts.mu.Lock()
	defer ts.mu.Unlock()
	ts.asks = append(ts.asks, d)
	return ts.chanOf(d)
}

func (ts *timers) chanOf(d time.Duration) chan time.Time {
	if ts.chs == nil {
		ts.chs = map[time.Duration]chan time.Time{}
	}
	ch, ok := ts.chs[d]
	if !ok {
		ch = make(chan time.Time, 1)
		ts.chs[d] = ch
	}
	return ch
}

func (ts *timers) fire(d time.Duration) {
	ts.mu.Lock()
	defer ts.mu.Unlock()
	ts.chanOf(d) <- time.Now()
}

func newRig(t *testing.T, script string) *rig {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	home := t.TempDir()
	for _, a := range []string{"main", "other"} {
		if err := os.MkdirAll(filepath.Join(home, "accounts", a), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	bin := filepath.Join(t.TempDir(), "claude")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\n"+script), 0o755); err != nil {
		t.Fatal(err)
	}
	r := &rig{t: t, home: home, timer: &timers{}}
	r.m = &Manager{Home: home, Wait: r.timer.wait, Lookup: func(string) (string, error) { return bin, nil },
		Changed: func(a string) { r.mu.Lock(); r.moved = append(r.moved, a); r.mu.Unlock() }}
	t.Cleanup(r.m.Close)
	return r
}

func (r *rig) state(account string) string {
	if l := r.m.Of(account); l != nil {
		return l.State
	}
	return ""
}

// grandchild is the pid the login script's background process wrote, once it has: the script is then past its forks.
func (r *rig) grandchild(account string) int {
	for i := 0; i < 300; i++ {
		b, _ := os.ReadFile(filepath.Join(r.home, "accounts", account, ".claude", "grandchild"))
		if pid, _ := strconv.Atoi(strings.TrimSpace(string(b))); pid > 0 {
			return pid
		}
		time.Sleep(10 * time.Millisecond)
	}
	return 0
}

func (r *rig) settles(account, want string) {
	r.t.Helper()
	for i := 0; i < 400; i++ {
		if r.state(account) == want {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	r.t.Fatalf("login of %s is %q, want %q", account, r.state(account), want)
}

// waitsOnStdin is a login that prints its URL, starts a grandchild that records its pid and then waits on stdin.
const waitsOnStdin = `echo "Open: ` + fakeURL + `"
echo "Paste code here if prompted >"
sleep 300 &
echo $! > "$CLAUDE_CONFIG_DIR/grandchild"
env | cut -d= -f1 | sort > "$CLAUDE_CONFIG_DIR/env"
cat > /dev/null
`

func TestStartAnswersTheURLAndOnlyThat(t *testing.T) {
	t.Setenv("DAEMON_ONLY_SECRET", "x")
	r := newRig(t, waitsOnStdin)
	l, err := r.m.Start("main")
	if err != nil {
		t.Fatal(err)
	}
	if l.URL != fakeURL || l.State != Waiting || !strings.HasPrefix(l.ID, "login_") {
		t.Fatalf("got %+v", l)
	}
	if got := r.m.Of("main"); got == nil || got.URL != fakeURL || got.ID != l.ID {
		t.Fatalf("Of = %+v", got)
	}
	dir := filepath.Join(r.home, "accounts", "main", ".claude")
	var names string
	for i := 0; i < 200 && !strings.Contains(names, "CLAUDE_CONFIG_DIR"); i++ {
		b, _ := os.ReadFile(filepath.Join(dir, "env"))
		names = string(b)
		time.Sleep(10 * time.Millisecond)
	}
	if strings.Contains(names, "DAEMON_ONLY_SECRET") {
		t.Fatalf("the daemon's environment reached the child:\n%s", names)
	}
	for _, want := range []string{"PATH", "HOME", "CLAUDE_CONFIG_DIR"} {
		if !strings.Contains(names, want) {
			t.Errorf("child environment lacks %s:\n%s", want, names)
		}
	}
}

func TestSecondLoginOnTheAccountIsRefusedAndAnotherIsNot(t *testing.T) {
	r := newRig(t, waitsOnStdin)
	if _, err := r.m.Start("main"); err != nil {
		t.Fatal(err)
	}
	var no *Refusal
	if _, err := r.m.Start("main"); !errors.As(err, &no) || !strings.Contains(no.Reason, "already logging in") {
		t.Fatalf("second login: %v", err)
	}
	if _, err := r.m.Start("other"); err != nil {
		t.Fatalf("another account: %v", err)
	}
}

func alive(pid int) bool { return syscall.Kill(pid, 0) == nil }

func TestCancelKillsTheGroupAndIsIdempotent(t *testing.T) {
	r := newRig(t, waitsOnStdin)
	l, err := r.m.Start("main")
	if err != nil {
		t.Fatal(err)
	}
	pid := r.grandchild("main")
	if pid == 0 || !alive(pid) {
		t.Fatalf("grandchild not running: %d", pid)
	}
	if err := r.m.Cancel(l.ID); err != nil {
		t.Fatal(err)
	}
	if got := r.state("main"); got != Cancelled {
		t.Fatalf("state %q", got)
	}
	for i := 0; i < 300 && alive(pid); i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if alive(pid) {
		t.Fatal("the grandchild outlived the cancel: the group was not killed")
	}
	if err := r.m.Cancel(l.ID); err != nil {
		t.Fatalf("second cancel: %v", err)
	}
	if got := r.state("main"); got != Cancelled {
		t.Fatalf("after the child was reaped: %q", got)
	}
	if err := r.m.Cancel("login_nope"); !errors.Is(err, ErrUnknown) {
		t.Fatalf("unknown id: %v", err)
	}
	if got := r.m.Of("main"); got.URL != "" {
		t.Errorf("an ended login keeps its URL: %+v", got)
	}
	if _, err := r.m.Start("main"); err != nil {
		t.Fatalf("a new login after the cancel: %v", err)
	}
}

func TestNoURLWithinTwentySecondsKillsTheChild(t *testing.T) {
	r := newRig(t, "echo $$ > \"$CLAUDE_CONFIG_DIR/pid\"\nsleep 300\n")
	errc := make(chan error, 1)
	go func() { _, err := r.m.Start("main"); errc <- err }()
	// Fire only once the manager asked for the URL timer.
	for i := 0; i < 400; i++ {
		r.timer.mu.Lock()
		asked := len(r.timer.asks) > 0
		r.timer.mu.Unlock()
		if asked {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	r.timer.fire(URLWait)
	var no *Refusal
	if err := <-errc; !errors.As(err, &no) || !strings.Contains(no.Reason, "20 seconds") {
		t.Fatalf("got %v", err)
	}
	b, _ := os.ReadFile(filepath.Join(r.home, "accounts", "main", ".claude", "pid"))
	pid, _ := strconv.Atoi(strings.TrimSpace(string(b)))
	for i := 0; i < 300 && pid > 0 && alive(pid); i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if pid > 0 && alive(pid) {
		t.Fatal("the child outlived the timeout")
	}
	if got := r.state("main"); got != Failed {
		t.Fatalf("state %q", got)
	}
}

func TestMaxRunKillsTheLogin(t *testing.T) {
	r := newRig(t, waitsOnStdin)
	if _, err := r.m.Start("main"); err != nil {
		t.Fatal(err)
	}
	r.grandchild("main")
	r.timer.fire(MaxRun)
	r.settles("main", Failed)
}

func TestExitWithTheCredentialIsDone(t *testing.T) {
	r := newRig(t, "echo "+fakeURL+"\nsleep 0.3\ntouch \"$CLAUDE_CONFIG_DIR/.credentials.json\"\n")
	if _, err := r.m.Start("main"); err != nil {
		t.Fatal(err)
	}
	r.settles("main", Done)
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.moved) < 2 || r.moved[len(r.moved)-1] != "main" {
		t.Fatalf("the accounts topic was told %v", r.moved)
	}
}

func TestExitWithoutTheCredentialOrWithAnErrorFails(t *testing.T) {
	t.Setenv("SANDBOX_RUNTIME", "") // a sandboxed process cannot tell, and an exit it cannot check is taken as done
	for name, script := range map[string]string{
		"no credential": "echo " + fakeURL + "\nsleep 0.2\n",
		"exit 1":        "echo " + fakeURL + "\nsleep 0.2\ntouch \"$CLAUDE_CONFIG_DIR/.credentials.json\"\nexit 1\n",
	} {
		t.Run(name, func(t *testing.T) {
			r := newRig(t, script)
			if _, err := r.m.Start("main"); err != nil {
				t.Fatal(err)
			}
			r.settles("main", Failed)
		})
	}
}

func TestNoURLAtAllEndsTheStart(t *testing.T) {
	r := newRig(t, "echo nothing to open here\n")
	var no *Refusal
	if _, err := r.m.Start("main"); !errors.As(err, &no) {
		t.Fatalf("got %v", err)
	}
	if got := r.state("main"); got != Failed {
		t.Fatalf("state %q", got)
	}
}

func TestUnavailableVendorAndMissingCLI(t *testing.T) {
	r := newRig(t, "exit 0\n")
	if err := os.WriteFile(filepath.Join(r.home, "accounts", "other", "vendor"), []byte("opencode\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	var no *Refusal
	if _, err := r.m.Start("other"); !errors.As(err, &no) {
		t.Fatalf("opencode: %v", err)
	}
	r.m.Lookup = func(string) (string, error) { return "", errors.New("not found") }
	if _, err := r.m.Start("main"); !errors.As(err, &no) {
		t.Fatalf("no CLI: %v", err)
	}
}

func TestCloseKillsTheChildren(t *testing.T) {
	r := newRig(t, waitsOnStdin)
	if _, err := r.m.Start("main"); err != nil {
		t.Fatal(err)
	}
	r.m.Close()
	if got := r.state("main"); got != Cancelled {
		t.Fatalf("state %q", got)
	}
	var no *Refusal
	if _, err := r.m.Start("other"); !errors.As(err, &no) {
		t.Fatalf("a closed manager started a login: %v", err)
	}
}
