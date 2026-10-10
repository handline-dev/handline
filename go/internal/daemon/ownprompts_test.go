package daemon

import (
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/term"
)

func startAt(t *testing.T, dir string, own bool) (*Daemon, error) {
	t.Helper()
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Auth: fakeAuth{}, OwnPrompts: own})
	if err == nil {
		t.Cleanup(d.Close)
	}
	return d, err
}

func homeDir(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "hl")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	return dir
}

// blankScreens is a terminal host whose every screen shows no prompt; reads counts the screens the watch asked for.
type blankScreens struct{ reads atomic.Int32 }

func (b *blankScreens) Name() string                                   { return "fake" }
func (b *blankScreens) Available() bool                                { return true }
func (b *blankScreens) ListTerminals() map[string]bool                 { return nil }
func (b *blankScreens) WaitIdle(string, int, func([]string) bool) bool { return true }
func (b *blankScreens) Send(string, string, bool) (bool, string)       { return true, "" }
func (b *blankScreens) ReadScreen(string, int) *term.Screen {
	b.reads.Add(1)
	return &term.Screen{Tail: []string{"nothing here", "❯"}, Source: "screen"}
}

// seedOpenPrompt leaves an open prompt_blocks row for an agent with a terminal, in the database under dir.
func seedOpenPrompt(t *testing.T, dir string) {
	t.Helper()
	d, err := startAt(t, dir, false)
	if err != nil {
		t.Fatal(err)
	}
	d.db.Exec("INSERT INTO agent_state(agent, state, updated, terminal) VALUES('manager', 'IDLE', 0, '%1')")
	d.db.Exec("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, answerable, first_seen, last_seen) VALUES('manager', 'question', 'f', 't', '[]', '{}', 1, 1, 1)")
	d.Close()
}

func startWithScreens(t *testing.T, dir string, own bool, screens *blankScreens) *Daemon {
	t.Helper()
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Auth: fakeAuth{}, OwnPrompts: own, Terminal: screens})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d
}

func goneAt(d *Daemon) (gone any) {
	d.db.QueryRow("SELECT gone_at FROM prompt_blocks WHERE agent = 'manager'").Scan(&gone)
	return gone
}

// Every daemon but a gateway takes the daemon singleton, so none starts beside a Python daemon and a second one is refused; without
// -own-prompts it writes nothing to prompt_blocks. The control is the same setup with -own-prompts, where the watch's first pass reads
// the blank screen and closes the row, so a daemon that watched anyway would fail the second half.
func TestADaemonStartedWithoutOwnPromptsTakesTheSingletonButDoesNotWatch(t *testing.T) {
	dir := homeDir(t)
	python := lock.TakeSingleton(dir, daemonLock) // the Python daemon is up
	if _, err := startAt(t, dir, false); err == nil || !strings.Contains(err.Error(), "another daemon holds this home") {
		t.Fatalf("it started beside a daemon that holds the home: %v", err)
	}
	python.Release()
	d, err := startAt(t, dir, false)
	if err != nil {
		t.Fatal(err)
	}
	if d.single == nil {
		t.Fatal("it took no singleton")
	}
	if _, err := startAt(t, dir, false); err == nil {
		t.Fatal("two daemons on one home")
	}
	d.Close()

	seedOpenPrompt(t, dir)
	control := startWithScreens(t, dir, true, &blankScreens{})
	deadline := time.Now().Add(5 * time.Second)
	for goneAt(control) == nil && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if goneAt(control) == nil {
		t.Fatal("control: the watching daemon never closed the row of a blank screen")
	}
	control.Close()

	dir = homeDir(t)
	seedOpenPrompt(t, dir)
	screens := &blankScreens{}
	d = startWithScreens(t, dir, false, screens)
	time.Sleep(500 * time.Millisecond) // the watch's first pass is immediate, so a watching daemon has run by now
	if n := screens.reads.Load(); n != 0 {
		t.Fatalf("it read %d screens", n)
	}
	if gone := goneAt(d); gone != nil {
		t.Fatalf("the open prompt row was closed: %v", gone)
	}
}

func TestAGatewayRefusesOwnPrompts(t *testing.T) {
	dir := homeDir(t)
	_, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Auth: fakeAuth{}, Gateway: true, OwnPrompts: true,
		Listen: "127.0.0.1:0", RelaySocket: filepath.Join(dir, "relay.sock"), PidFile: filepath.Join(dir, "pid")})
	if err == nil || !strings.Contains(err.Error(), "-own-prompts") {
		t.Fatalf("a gateway started with -own-prompts: %v", err)
	}
}

func TestTheScreenWatchRunsOnlyForTheDaemonThatTakesTheSingleton(t *testing.T) {
	dir := homeDir(t)
	python := lock.TakeSingleton(dir, daemonLock)
	if _, err := startAt(t, dir, true); err == nil || !strings.Contains(err.Error(), "another daemon holds this home") {
		t.Fatalf("it started beside a daemon that holds the home: %v", err)
	}
	python.Release()
	first, err := startAt(t, dir, true)
	if err != nil {
		t.Fatal(err)
	}
	if first.single == nil {
		t.Fatal("the watching daemon holds no singleton")
	}
	if _, err := startAt(t, dir, true); err == nil {
		t.Fatal("two watching daemons on one home")
	}
	first.Close()
	if second, err := startAt(t, dir, true); err != nil || second.single == nil {
		t.Fatalf("the singleton is not released on Close: %v", err)
	}
}

// Structured sessions are kept to one launch by a lock inside the process, so a second process on the home would launch them again.
func TestASecondDaemonRunningStructuredSessionsOnTheHomeIsRefused(t *testing.T) {
	dir := homeDir(t)
	start := func() (*Daemon, error) {
		return Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Auth: fakeAuth{}, Structured: func(string) bool { return true }})
	}
	first, err := start()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := start(); err == nil {
		t.Fatal("a second daemon started beside the first")
	}
	first.Close()
	second, err := start()
	if err != nil {
		t.Fatalf("the hold outlived its daemon: %v", err)
	}
	second.Close()
}

func TestADaemonMakesTheFounderKeyBeforeItServes(t *testing.T) {
	dir := homeDir(t)
	made := false
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Auth: fakeAuth{}, EnsureFounderKey: func() bool { made = true; return true }})
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if !made {
		t.Fatal("no founder key was made")
	}
}
