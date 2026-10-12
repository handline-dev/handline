package term

import (
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Host is what a backend adds to start and end the terminals the daemon runs agents in. Only tmux has it: Orca is unmaintained.
type Host interface {
	// CreateTerminal opens a window in cwd, types command at its shell and answers the new pane's handle. A handle "" with why "" is a
	// window that started whose handle could not be read.
	CreateTerminal(command, cwd, title string) (handle, why string)
	// CloseTerminal closes the pane's window (its pane alone when tab is false).
	CloseTerminal(handle string, tab bool) (bool, string)
	// RespawnTerminal ends everything running in the pane and leaves a fresh shell there, in the pane's directory.
	RespawnTerminal(handle string) (bool, string)
}

const (
	// A detached session has no client to size it, and a TUI drawn at 80x24 hides what the screen readers look for.
	sessionCols, sessionRows = "200", "50"
)

var windowNameRE = regexp.MustCompile(`[.:]`)

func (t Tmux) serverUp() bool {
	code, _, _ := t.tmux(tmuxTimeout, "", "has-session", "-t", tmuxSession)
	return code == 0
}

func (t Tmux) CreateTerminal(command, cwd, title string) (string, string) {
	var args []string
	if title != "" {
		args = append(args, "-n", windowNameRE.ReplaceAllString(title, "_"))
	}
	if cwd != "" {
		args = append(args, "-c", cwd)
	}
	// The home the daemon runs on goes into the window's environment, so what runs there reads the same one under either spelling.
	if home := getenv("HOME"); home != "" {
		args = append(args, "-e", "OCP_HOME="+home, "-e", "HANDLINE_HOME="+home)
	}
	args = append(args, "-P", "-F", "#{pane_id}")
	var code int
	var out, err string
	if t.serverUp() {
		code, out, err = t.tmux(tmuxTimeout, "", append([]string{"new-window", "-t", tmuxSession + ":"}, args...)...)
	} else {
		code, out, err = t.tmux(tmuxTimeout, "", append([]string{"new-session", "-d", "-s", tmuxSession, "-x", sessionCols, "-y", sessionRows}, args...)...)
	}
	pane := strings.TrimSpace(out)
	if code != 0 || !paneRE.MatchString(pane) {
		if err == "" {
			err = "tmux gave no pane id (" + pyRepr(cut(pane, 80)) + ")"
		}
		return "", err
	}
	if ok, why := t.Send(pane, command, true); !ok {
		t.CloseTerminal(pane, true)
		return "", "could not type the command into the new window: " + why
	}
	return pane, ""
}

func (t Tmux) CloseTerminal(handle string, tab bool) (bool, string) {
	if !paneRE.MatchString(handle) {
		return false, "not a tmux pane: " + pyRepr(handle)
	}
	kill := "kill-pane"
	if tab {
		kill = "kill-window"
	}
	code, _, err := t.tmux(tmuxTimeout, "", kill, "-t", handle)
	return code == 0, err
}

func (t Tmux) RespawnTerminal(handle string) (bool, string) {
	if !paneRE.MatchString(handle) {
		return false, "not a tmux pane: " + pyRepr(handle)
	}
	code, _, err := t.tmux(tmuxTimeout, "", "respawn-pane", "-k", "-t", handle)
	return code == 0, err
}

// PanePid is the pid of the process the pane runs (its shell); 0 when tmux cannot say.
func (t Tmux) PanePid(handle string) int {
	if !paneRE.MatchString(handle) {
		return 0
	}
	code, out, _ := t.tmux(tmuxTimeout, "", "display-message", "-p", "-t", handle, "#{pane_pid}")
	pid, err := strconv.Atoi(strings.TrimSpace(out))
	if code != 0 || err != nil {
		return 0
	}
	return pid
}

type panePidder interface{ PanePid(handle string) int }

func (d dynamic) PanePid(handle string) int {
	if h, ok := d.cur().(panePidder); ok {
		return h.PanePid(handle)
	}
	return 0
}

// PanePid is the pid of the process the terminal runs; 0 when the host cannot say.
func (t *Term) PanePid(handle string) int {
	if h, ok := t.Backend.(panePidder); ok {
		return h.PanePid(handle)
	}
	return 0
}

func (d dynamic) host() Host {
	h, _ := d.cur().(Host)
	return h
}

func (d dynamic) CreateTerminal(command, cwd, title string) (string, string) {
	if h := d.host(); h != nil {
		return h.CreateTerminal(command, cwd, title)
	}
	return "", "the " + d.Name() + " terminal host cannot start terminals"
}

func (d dynamic) CloseTerminal(handle string, tab bool) (bool, string) {
	if h := d.host(); h != nil {
		return h.CloseTerminal(handle, tab)
	}
	return false, "the " + d.Name() + " terminal host cannot close terminals"
}

func (d dynamic) RespawnTerminal(handle string) (bool, string) {
	if h := d.host(); h != nil {
		return h.RespawnTerminal(handle)
	}
	return false, "the " + d.Name() + " terminal host cannot restart a terminal's shell"
}

func (t *Term) host() Host { h, _ := t.Backend.(Host); return h }

// CreateTerminal starts a terminal running command, with the reason it could not.
func (t *Term) CreateTerminal(command, cwd, title string) (string, string) {
	if h := t.host(); h != nil {
		return h.CreateTerminal(command, cwd, title)
	}
	return "", "the " + t.Backend.Name() + " terminal host cannot start terminals"
}

// CloseTerminal closes the terminal and its tab.
func (t *Term) CloseTerminal(handle string) (bool, string) {
	if h := t.host(); h != nil {
		return h.CloseTerminal(handle, true)
	}
	return false, "the " + t.Backend.Name() + " terminal host cannot close terminals"
}

// CloseSession ends the agent's session and leaves its terminal standing, with a shell at the prompt in the same directory. The program
// is ended through the host, not by typing an exit command at it: no key reaches a session after its launch. The same guards as before
// apply, since ending a session mid-turn throws away work nothing has recorded: it waits for the turn to end and does nothing at a modal.
// "Ok" means the shell was restarted; whether the session's process ended is the caller's to confirm.
func (t *Term) CloseSession(handle string) (bool, string) {
	if !t.Alive(handle) {
		return false, "terminal " + handle + " is not alive"
	}
	if !t.WaitIdle(handle, IdleTimeoutMs) {
		return false, "agent is mid-turn; not closing"
	}
	if blocked := t.ScreenBlocker(handle); blocked != nil {
		return false, blocked.Reason() + "; not ending the session under it"
	}
	h := t.host()
	if h == nil {
		return false, "the " + t.Backend.Name() + " terminal host cannot restart a terminal's shell"
	}
	if ok, why := h.RespawnTerminal(handle); !ok {
		return false, "could not end the session: " + cut(why, 200)
	}
	return true, "session ended"
}

// LaunchEnterDelay is between a launch line and the Enter that runs it.
const LaunchEnterDelay = 500 * time.Millisecond

func (t *Term) sleep(d time.Duration) {
	if t.Sleep != nil {
		t.Sleep(d)
		return
	}
	time.Sleep(d)
}

func (t *Term) now() time.Time {
	if t.Now != nil {
		return t.Now()
	}
	return time.Now()
}

// Relaunch starts an agent again in the shell its session left behind: the launch line is the one keystroke the daemon sends, because
// the command is ours and the account comes back with it. started is asked, before the wait, whether the command started something:
// sending only says it was typed, and the launch refuses over the 5h limit and exits when the account is not logged in.
func (t *Term) Relaunch(handle, command string, started func() bool) (bool, string) {
	if !t.Alive(handle) {
		return false, "terminal " + handle + " is not alive"
	}
	if session, _ := t.SessionOnScreen(handle); session {
		// Whatever the process table says, a session is drawn there: the line would reach it as a prompt.
		return false, "a session is on the terminal's screen; not typing a launch line at it"
	}
	// The line and its Enter go as two sends, in case an Enter that arrives with the line is swallowed by a shell taking it as a paste.
	ok, out := t.Send(handle, command, false)
	if ok {
		t.sleep(LaunchEnterDelay)
		ok, out = t.Send(handle, "", true)
	}
	if !ok {
		return false, "could not send the launch command: " + cut(out, 200)
	}
	if started != nil && !started() {
		return false, "the launch command was typed but no session started" + t.terminalNote(handle)
	}
	// Typing into a TUI that is still booting loses the opening characters.
	t.WaitIdle(handle, IdleTimeoutMs)
	return true, "relaunched"
}

// terminalNote is what a launch that started nothing says about the terminal it was typed into: that it is gone, or alive and what its
// last rows show. One line, cut short: it is text an agent's shell drew.
func (t *Term) terminalNote(handle string) string {
	if !t.Alive(handle) {
		return "; the terminal is gone"
	}
	scr := t.screen(handle, 40)
	if scr == nil {
		return "; the terminal is alive but its screen could not be read"
	}
	var rows []string
	for _, r := range scr.Tail {
		if r = oneline(r); r != "" {
			rows = append(rows, r)
		}
	}
	if len(rows) > 3 {
		rows = rows[len(rows)-3:]
	}
	if len(rows) == 0 {
		return "; the terminal is alive"
	}
	return "; the terminal is alive (screen: " + cut(strings.Join(rows, " | "), 240) + ")"
}

// BypassPromptShowing is whether this session sits at its vendor's bypass-permissions warning: the cursor starts on the option that
// exits, and an Enter typed there would take it.
func (t *Term) BypassPromptShowing(handle string) bool {
	_, shown := t.bypassState(handle)
	return shown
}

func (t *Term) bypassState(handle string) (option string, shown bool) {
	text, ok := t.ScreenText(handle)
	if !ok {
		return "", false
	}
	return t.driver(handle).BypassScreen(strings.Split(text, "\n"))
}

const (
	promptWait = 5 * time.Second
	promptPoll = 500 * time.Millisecond
)

// AcceptBypass accepts the bypass-permissions warning this terminal shows, callers having decided that they may. Enter is pressed only with
// the cursor on the accepting option, read off the screen: from the exiting one a single Down is sent first and the screen read again, and a
// cursor anywhere else, or unreadable, presses nothing. True once the warning is gone.
func (t *Term) AcceptBypass(handle string) (bool, string) {
	drv := t.driver(handle)
	accept := drv.BypassAccept()
	state, shown := t.bypassState(handle)
	if !shown {
		return false, "the bypass-permissions warning is not on screen"
	}
	if state != accept {
		if ok, out := t.Send(handle, drv.BypassNextKey(), false); !ok {
			return false, "could not move to " + pyRepr(accept) + ": " + cut(out, 200)
		}
		deadline := t.now().Add(promptWait)
		for {
			if state, _ := t.bypassState(handle); state == accept {
				break
			}
			if !t.now().Before(deadline) {
				return false, "the cursor did not reach " + pyRepr(accept) + "; nothing was confirmed"
			}
			t.sleep(promptPoll)
		}
	}
	if ok, out := t.Send(handle, "", true); !ok {
		return false, "could not confirm the bypass warning: " + cut(out, 200)
	}
	deadline := t.now().Add(promptWait)
	for t.BypassPromptShowing(handle) {
		if !t.now().Before(deadline) {
			return false, "the bypass warning is still on screen after Enter"
		}
		t.sleep(promptPoll)
	}
	return true, "accepted"
}

// WaitForTrustPrompt polls until the trust prompt shows (or, with showing false, is gone) or gives up: the vendor's redraw after
// the answer is not instant, and reading on the very next line risks the screen it was still drawing.
func (t *Term) WaitForTrustPrompt(handle string, showing bool) bool {
	deadline := t.now().Add(promptWait)
	for t.TrustPromptShowing(handle) != showing {
		if !t.now().Before(deadline) {
			return false
		}
		t.sleep(promptPoll)
	}
	return true
}

// DriverScope holds one driver lookup for the calls made inside it; the returned func ends it. The lookup here is a function of the
// handle with nothing to hold.
func (t *Term) DriverScope() func() { return func() {} }

// ComposerOnScreen is what the composer box on this terminal's screen holds; ok is false when the screen cannot be read or shows no box.
func (t *Term) ComposerOnScreen(handle string) (string, bool) {
	scr := t.screen(handle, 40)
	if scr == nil {
		return "", false
	}
	return ComposerText(scr.Tail)
}

// LastTail is the rows the terminal draws now, nil when its screen could not be read.
func (t *Term) LastTail(handle string) []string {
	if scr := t.screen(handle, 40); scr != nil {
		return scr.Tail
	}
	return nil
}

// Missing is why the host cannot be used, for a refusal that names it.
func (t *Term) Missing() string {
	if t.Backend.Available() {
		return ""
	}
	return "the " + t.Backend.Name() + " terminal host is not available"
}
