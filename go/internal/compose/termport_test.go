package compose

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/turns"
)

// fakeTerminals is a terminal host with no tmux: it opens panes, records what is typed and ended, and shows a screen per pane.
type fakeTerminals struct {
	mu      sync.Mutex
	live    map[string]bool
	opened  []string // "cwd|title|command"
	sent    []string
	respawn []string
	closed  []string
	screen  []string
	panePid map[string]int // the process each pane runs; none reads as unknown
	// started runs when a terminal is opened, as the launch line would start a session in it.
	started func(cwd string)
	// running, when set, makes the host list its panes: each live one runs it in front of its shell.
	running string
}

func newFakeTerminals() *fakeTerminals {
	return &fakeTerminals{live: map[string]bool{}, screen: []string{"~/work $ "}}
}

func (f *fakeTerminals) Name() string                                   { return "fake" }
func (f *fakeTerminals) Available() bool                                { return true }
func (f *fakeTerminals) WaitIdle(string, int, func([]string) bool) bool { return true }
func (f *fakeTerminals) ListTerminals() map[string]bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := map[string]bool{}
	for h := range f.live {
		out[h] = true
	}
	return out
}

func (f *fakeTerminals) PanePid(handle string) int { return f.panePid[handle] }

// ListPanes is the panes, or (nil, false) when the host is not set to list them.
func (f *fakeTerminals) ListPanes() ([]term.Pane, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.running == "" {
		return nil, false
	}
	var panes []term.Pane
	for h := range f.live {
		panes = append(panes, term.Pane{Handle: h, Command: f.running})
	}
	return panes, true
}
func (f *fakeTerminals) ReadScreen(string, int) *term.Screen {
	return &term.Screen{Tail: f.screen, Source: "screen"}
}
func (f *fakeTerminals) Send(_, text string, enter bool) (bool, string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sent = append(f.sent, text)
	return true, ""
}
func (f *fakeTerminals) CreateTerminal(command, cwd, title, agent string) (string, string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	handle := "%" + string(rune('1'+len(f.opened)))
	f.live[handle] = true
	f.opened = append(f.opened, cwd+"|"+title+"|"+command)
	if f.started != nil {
		f.started(cwd)
	}
	// `hl launch --prompt-file F` reads the file and deletes it: the daemon knows its session took the prompt by the file being gone.
	if _, file, ok := strings.Cut(command, "--prompt-file "); ok {
		os.Remove(strings.Fields(file)[0])
	}
	return handle, ""
}
func (f *fakeTerminals) CloseTerminal(handle string, _ bool) (bool, string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.live, handle)
	f.closed = append(f.closed, handle)
	return true, ""
}
func (f *fakeTerminals) RespawnTerminal(handle string) (bool, string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.respawn = append(f.respawn, handle)
	return true, ""
}

func (f *fakeTerminals) term() *term.Term {
	t := term.New(f, nil)
	t.Settle = 0
	return t
}

func gitRepo(t *testing.T) string {
	t.Helper()
	repo := t.TempDir()
	for _, args := range [][]string{{"init", "-q", "-b", "main"}, {"-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"}} {
		if out, err := exec.Command("git", append([]string{"-C", repo}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("%v: %s", err, out)
		}
	}
	return repo
}

// sessionRecordIn is a started hook that writes the record `hl launch` would leave under config dir for a running session in cwd.
func sessionRecordIn(dir string) func(cwd string) {
	return func(cwd string) {
		os.MkdirAll(filepath.Join(dir, "sessions"), 0o755)
		os.WriteFile(filepath.Join(dir, "sessions", strconv.Itoa(os.Getpid())+".json"), []byte(`{"cwd":"`+cwd+`","sessionId":"s"}`), 0o644)
	}
}

func TestATerminalAgentIsStartedInANewWorktreeThroughTheHostAndItsHandleKept(t *testing.T) {
	ft := newFakeTerminals()
	p, lc, _, _ := wiredWith(t, ft)
	d := p.db
	repo, _ := filepath.EvalSymlinks(gitRepo(t))
	// the session `hl launch` would start: its record under the account's config directory, for a process that is running
	dir, _ := team.AccountConfigDir(p.home, "a1")
	if err := dbpkg.SetAccount(d, "bob", "a1", "recorded"); err != nil {
		t.Fatal(err)
	}
	ft.started = sessionRecordIn(dir)
	os.WriteFile(filepath.Join(p.home, "agents", "bob", "agent.yaml"), []byte("vendor: claude\nrepo: "+repo+"\n"), 0o644)
	ok, said, err := lc.Spawns.SpawnWith("bob", repo, lifecycle.SpawnOpts{})
	if err != nil {
		t.Fatal(err)
	}
	if !ok || strings.Contains(said, "WARNING") {
		t.Fatalf("a session runs in it: %v %q", ok, said)
	}
	if len(ft.opened) != 1 || !strings.HasSuffix(ft.opened[0], "|bob|"+lifecycle.CommandFor("", "", "", "bob")) ||
		!strings.HasPrefix(ft.opened[0], repo+".worktrees/bob|") {
		t.Fatalf("%v %q opened %q", ok, said, ft.opened)
	}
	if h, _ := dbpkg.Terminal(d, "bob"); h != "%1" {
		t.Fatalf("the handle of the terminal is kept: %q", h)
	}
	if _, err := os.Stat(repo + ".worktrees/bob"); err != nil {
		t.Fatal("the worktree was made beside the repo")
	}
}

func TestAClosedTerminalAgentIsEndedAtItsShellAndItsTerminalClosed(t *testing.T) {
	ft := newFakeTerminals()
	ft.screen = []string{strings.Repeat("─", 40), "│ > │", strings.Repeat("─", 40), "  ? for shortcuts"}
	_, lc, _, _ := wiredWith(t, ft)
	tp := termPort{lc.Manager.Term.(termPort).t}
	ft.live["%9"] = true
	if ok, why := tp.CloseSession("%9"); !ok || len(ft.sent) != 0 || len(ft.respawn) != 1 {
		t.Fatalf("%v %q sent %q", ok, why, ft.sent)
	}
	if ok, _ := tp.CloseTerminal("%9"); !ok || len(ft.closed) != 1 {
		t.Fatal("the terminal is closed")
	}
}

func TestAWakesLaunchIsShownStartingAtOnceAndUntilItsSessionsFirstHook(t *testing.T) {
	ft := newFakeTerminals()
	ft.running = "claude"
	p, _, env, _ := wiredWith(t, ft)
	d := p.db
	tracker := &turns.Tracker{Agent: func(h string) string { a, _ := dbpkg.AgentOfTerminal(d, h); return a }}
	cfg := cfgOf(p)
	cfg.Turns = tracker
	lc := p.Lifecycle(cfg, LifecycleConfig{Env: env})
	p.Tick(cfg, TickConfig{Terminals: true}) // wires the tracker's changes to the view; it never ticks here
	wt, _ := filepath.EvalSymlinks(t.TempDir())
	dir, _ := team.AccountConfigDir(p.home, "a1")
	dbpkg.SetAccount(d, "bob", "a1", "recorded")
	ft.started = sessionRecordIn(dir)
	dbpkg.SetTerminal(d, "bob", "", wt)
	dbpkg.SetClosed(d, "bob", "11111111-1111-4111-8111-111111111111")
	dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "human", Target: "bob", Body: "begin", Response: "async", Kind: "ask"})
	state := func() string {
		t.Helper()
		var raw string
		if err := d.QueryRow("SELECT view FROM agent_view WHERE agent = 'bob'").Scan(&raw); err != nil {
			t.Fatalf("no row: %v", err)
		}
		var v map[string]any
		json.Unmarshal([]byte(raw), &v)
		s, _ := v["state"].(string)
		return s
	}
	if ok, said, err := lc.Manager.WakeAgent("bob", lifecycle.WakeOpts{Warm: true}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	// hl launch has kept no start of its own here, and nothing ticked: the daemon's own launch said so
	if s := state(); s != "starting" {
		t.Fatalf("after the launch: %s, want starting", s)
	}
	dbpkg.AddEvent(d, "bob", "SessionStart") // what the hook recorder writes, then tells the tracker
	tracker.Hook("bob", "SessionStart", map[string]any{}, time.Now().Unix())
	if s := state(); s == "starting" {
		t.Fatal("the session's first hook ends starting")
	}
}

func TestAClosedTerminalAgentIsWokenInANewTerminalResumingItsSession(t *testing.T) {
	ft := newFakeTerminals()
	p, lc, _, _ := wiredWith(t, ft)
	d := p.db
	wt, _ := filepath.EvalSymlinks(t.TempDir())
	dir, _ := team.AccountConfigDir(p.home, "a1")
	if err := dbpkg.SetAccount(d, "bob", "a1", "recorded"); err != nil {
		t.Fatal(err)
	}
	ft.started = sessionRecordIn(dir)
	dbpkg.SetTerminal(d, "bob", "", wt) // its pane was closed with its session
	if err := dbpkg.SetClosed(d, "bob", "11111111-1111-4111-8111-111111111111"); err != nil {
		t.Fatal(err)
	}
	if _, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "human", Target: "bob", Body: "begin", Response: "async", Kind: "ask"}); err != nil {
		t.Fatal(err)
	}
	ok, said, err := lc.Manager.WakeAgent("bob", lifecycle.WakeOpts{Warm: true})
	if err != nil || !ok {
		t.Fatalf("%v %q %v (opened %q)", ok, said, err, ft.opened)
	}
	if len(ft.opened) != 1 || !strings.HasPrefix(ft.opened[0], wt+"|bob|") || !strings.Contains(ft.opened[0], "--resume 11111111-1111-4111-8111-111111111111") {
		t.Fatalf("opened %q", ft.opened)
	}
	if h, _ := dbpkg.Terminal(d, "bob"); h != "%1" {
		t.Fatalf("the new terminal's handle: %q", h)
	}
}
