package compose

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/term"
)

// This file is the terminal an agent runs in, as the lifecycle and the telling layer see it: a tmux window on the daemon's own server, in
// which `hl launch` starts the vendor's session. The daemon launches (one typed line), ends a session, closes a terminal and looks at the
// screen; it types nothing at a running session. Messages reach a session by its inbox, so Send, SendToSession and ClearContext say
// they are not served here.

// notTyped is what a typed delivery answers: nothing is typed at a running terminal session.
const notTyped = "typing at a terminal session is not served by this daemon: messages go in through the session inbox"

// termPort is lifecycle.Terminal and supervisor.Terminal over one Term; termHost is lifecycle.Host over the same one.
type termPort struct{ t *term.Term }

type termHost struct{ t *term.Term }

// newTerm is the Term over the host in force for home (tmux unless config.yaml says otherwise). The driver is the default vendor's: the
// vendor of the agent in a terminal is not looked up from its handle.
func newTerm(home string) *term.Term { return term.New(term.Dynamic(home), nil) }

var (
	_ lifecycle.Terminal  = termPort{}
	_ lifecycle.Host      = termHost{}
	_ supervisor.Terminal = termPort{}
)

func (p termPort) Available() bool { return p.t.Backend.Available() }
func (p termPort) Missing() string { return p.t.Missing() }

func (p termPort) WaitIdle(handle string) bool                 { return p.t.WaitIdle(handle, term.IdleTimeoutMs) }
func (p termPort) TypeableNow(handle string) bool              { return p.t.TypeableNow(handle) }
func (p termPort) ScreenBlocker(handle string) *term.Blocker   { return p.t.ScreenBlocker(handle) }
func (p termPort) BackgroundWork(handle string) (string, bool) { return p.t.BackgroundWork(handle) }

// The routine dialogs, answered at launch: the folder-trust screen and the bypass-permissions warning. The lifecycle decides whether to
// press (a folder the founder declared, an agent whose skip-permissions is granted); these only read and press.
func (p termPort) TrustPromptShowing(handle string) bool     { return p.t.TrustPromptShowing(handle) }
func (p termPort) TrustPromptFolder(handle string) string    { return p.t.TrustPromptFolder(handle) }
func (p termPort) ConfirmTrust(handle string) (bool, string) { return p.t.ConfirmTrust(handle) }
func (p termPort) BypassPromptShowing(handle string) bool    { return p.t.BypassPromptShowing(handle) }
func (p termPort) AcceptBypass(handle string) (bool, string) { return p.t.AcceptBypass(handle) }
func (p termPort) WaitForTrustPrompt(handle string, showing bool) {
	p.t.WaitForTrustPrompt(handle, showing)
}
func (p termPort) DriverScope() func() { return p.t.DriverScope() }

func (p termPort) CloseSession(handle string) (bool, string)  { return p.t.CloseSession(handle) }
func (p termPort) CloseTerminal(handle string) (bool, string) { return p.t.CloseTerminal(handle) }
func (p termPort) Relaunch(handle, cmd string, started func() bool) (bool, string) {
	return p.t.Relaunch(handle, cmd, started)
}

// ResumeWith would type the handoff's pointer at the cleared session, which this daemon does not do.
func (p termPort) ResumeWith(string, string) (bool, string) { return false, notTyped }

// supervisor.Terminal
func (p termPort) Alive(handle string) bool                      { return p.t.Alive(handle) }
func (p termPort) SessionOnScreen(handle string) (bool, bool)    { return p.t.SessionOnScreen(handle) }
func (p termPort) ComposerOnScreen(handle string) (string, bool) { return p.t.ComposerOnScreen(handle) }
func (p termPort) IsTextTyped(box, text string) bool             { return term.IsTextTyped(box, text) }
func (p termPort) LastTail(handle string) []string               { return p.t.LastTail(handle) }
func (p termPort) Send(string, string, bool) (bool, string)      { return false, notTyped }
func (p termPort) SendToSession(string, string) (bool, string)   { return false, notTyped }
func (p termPort) SendCtrlC(string)                              {}
func (p termPort) ClearContext(string) (bool, string)            { return false, notTyped }
func (p termPort) ClearCommand(string) string                    { return "/clear" }

// lifecycle.Host
func (h termHost) Available() bool { return h.t.Backend.Available() }
func (h termHost) Missing() string { return h.t.Missing() }
func (h termHost) OpenTerminal(cmd, cwd, title string) (string, string) {
	return h.t.CreateTerminal(cmd, cwd, title)
}

// CreateWorktree is a sibling directory `<repo>.worktrees/<name>` on a new branch `<name>`; a name already taken becomes `<name>-2`,
// as Orca did, rather than touching the first.
func (termHost) CreateWorktree(repo, name, baseRef string) (string, string) {
	root, err := filepath.EvalSymlinks(repo)
	if err != nil {
		root = repo
	}
	root, _ = filepath.Abs(root)
	parent := filepath.Join(filepath.Dir(root), filepath.Base(root)+".worktrees")
	for n := 1; n < 10; n++ {
		leaf := name
		if n > 1 {
			leaf = fmt.Sprintf("%s-%d", name, n)
		}
		path := filepath.Join(parent, leaf)
		if _, err := os.Lstat(path); err == nil {
			continue
		}
		r := gitrun.RunTimeout(root, 120*time.Second, "worktree", "add", "--no-track", "-b", leaf, path, baseRef)
		if r.RC == 0 {
			return path, ""
		}
		why := strings.TrimSpace(r.Stderr)
		if why == "" {
			why = strings.TrimSpace(r.Stdout)
		}
		if strings.Contains(why, "already exists") { // the branch name is taken; try the next
			continue
		}
		return "", "worktree create failed: " + why
	}
	return "", fmt.Sprintf("worktree create failed: %s through %s-9 are all taken", name, name)
}

func (termHost) Manual(wt, repo, name, baseRef, command string) string {
	return fmt.Sprintf("git -C %s worktree add -b %s %s %s", repo, name, wt, baseRef)
}
func (h termHost) WaitIdle(handle string, ms int) bool { return h.t.WaitIdle(handle, ms) }

// Submit would type the first prompt at the new session; it goes in through the session inbox, so none is typed here and the spawn reports
// that the prompt did not land.
func (termHost) Submit(string, string, int) {}

// The orchestration runs were Orca's: tmux has none.
func (termHost) HasRuns() bool                          { return false }
func (termHost) RunCurrent() string                     { return "" }
func (termHost) RunExists(string) bool                  { return false }
func (termHost) RunUse(string)                          {}
func (termHost) RunCreate(string) string                { return "" }
func (termHost) TaskCreate(_, _, _, _, _ string) string { return "" }
func (termHost) Dispatch(_, _, _ string) string         { return "" }
