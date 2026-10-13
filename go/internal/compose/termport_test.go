package compose

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
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
func (f *fakeTerminals) ReadScreen(string, int) *term.Screen {
	return &term.Screen{Tail: f.screen, Source: "screen"}
}
func (f *fakeTerminals) Send(_, text string, enter bool) (bool, string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sent = append(f.sent, text)
	return true, ""
}
func (f *fakeTerminals) CreateTerminal(command, cwd, title string) (string, string) {
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

func TestATerminalAgentIsStartedInANewWorktreeThroughTheHostAndItsHandleKept(t *testing.T) {
	ft := newFakeTerminals()
	p, lc, _, _ := wiredWith(t, ft)
	d := p.Sink.DB
	repo, _ := filepath.EvalSymlinks(gitRepo(t))
	// the session `hl launch` would start: its record under the account's config directory, for a process that is running
	dir, _ := team.AccountConfigDir(p.home, "a1")
	if err := dbpkg.SetAccount(d, "bob", "a1", "recorded"); err != nil {
		t.Fatal(err)
	}
	ft.started = func(cwd string) {
		os.MkdirAll(filepath.Join(dir, "sessions"), 0o755)
		os.WriteFile(filepath.Join(dir, "sessions", strconv.Itoa(os.Getpid())+".json"), []byte(`{"cwd":"`+cwd+`","sessionId":"s"}`), 0o644)
	}
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

func TestAClosedTerminalAgentIsWokenInANewTerminalResumingItsSession(t *testing.T) {
	ft := newFakeTerminals()
	p, lc, _, _ := wiredWith(t, ft)
	d := p.Sink.DB
	wt, _ := filepath.EvalSymlinks(t.TempDir())
	dir, _ := team.AccountConfigDir(p.home, "a1")
	if err := dbpkg.SetAccount(d, "bob", "a1", "recorded"); err != nil {
		t.Fatal(err)
	}
	ft.started = func(cwd string) {
		os.MkdirAll(filepath.Join(dir, "sessions"), 0o755)
		os.WriteFile(filepath.Join(dir, "sessions", strconv.Itoa(os.Getpid())+".json"), []byte(`{"cwd":"`+cwd+`","sessionId":"s"}`), 0o644)
	}
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
