package term

import (
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
)

// Host is what a backend adds to start and end the terminals the daemon runs agents in.
type Host interface {
	// CreateTerminal opens a window in cwd for agent, stamps its pane with the agent, types command at its shell and answers the new
	// pane's handle. A handle "" with why "" is a window that started whose handle could not be read.
	CreateTerminal(command, cwd, title, agent string) (handle, why string)
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

// AgentOption is the tmux pane option that says which agent the daemon opened the pane for; a pane without it is not ours to close.
const AgentOption = "@hl_agent"

func (t Tmux) CreateTerminal(command, cwd, title, agent string) (string, string) {
	pane, why := t.newPane(cwd, title, "")
	if pane == "" {
		return "", why
	}
	if agent != "" {
		if code, _, err := t.tmux(tmuxTimeout, "", "set-option", "-p", "-t", pane, AgentOption, agent); code != 0 {
			t.CloseTerminal(pane, true)
			return "", "could not stamp the new window with its agent: " + err
		}
	}
	if ok, why := t.Send(pane, command, true); !ok {
		t.CloseTerminal(pane, true)
		return "", "could not type the command into the new window: " + why
	}
	return pane, ""
}

// ShellOption is the tmux pane option that names the agent a plain shell was opened beside; such a pane is the person's, not an agent's.
const ShellOption = "@hl_shell"

// CreateShell opens a window in cwd running the user's login shell, stamped as a shell of agent.
func (t Tmux) CreateShell(cwd, title, agent string) (string, string) {
	pane, why := t.newPane(cwd, title, `exec "${SHELL:-/bin/zsh}" -l`)
	if pane == "" {
		return "", why
	}
	if code, _, err := t.tmux(tmuxTimeout, "", "set-option", "-p", "-t", pane, ShellOption, agent); code != 0 {
		t.CloseTerminal(pane, true)
		return "", "could not stamp the new window as a shell: " + err
	}
	return pane, ""
}

// RunOption is the tmux pane option that tags a window the daemon opened to run a command ("<message id>:<n>@<start>"); a pane id comes
// back after a restart of the tmux server, the tag does not.
const RunOption = "@hl_run"

// CreateRun opens a window in cwd, stamped as a shell of agent and tagged tag, whose own command is `$SHELL -lc command`. The window
// stays after the command ends (remain-on-exit), so tmux keeps its exit status and end time. The option is set on a plain window that
// is then respawned with the command, so a command that ends at once cannot close it first.
func (t Tmux) CreateRun(cwd, title, agent, tag, command string) (string, string) {
	pane, why := t.newPane(cwd, title, "")
	if pane == "" {
		return "", why
	}
	for _, o := range [][]string{{ShellOption, agent}, {RunOption, tag}, {"remain-on-exit", "on"}} {
		if code, _, err := t.tmux(tmuxTimeout, "", "set-option", "-p", "-t", pane, o[0], o[1]); code != 0 {
			t.CloseTerminal(pane, true)
			return "", "could not set up the new window: " + err
		}
	}
	if code, _, err := t.tmux(tmuxTimeout, "", "respawn-pane", "-k", "-t", pane, "/bin/sh", "-c", `exec "${SHELL:-/bin/zsh}" -lc "$1"`, "sh", command); code != 0 {
		t.CloseTerminal(pane, true)
		return "", "could not run the command in the new window: " + err
	}
	return pane, ""
}

// PaneRun is what tmux says of a window CreateRun opened.
type PaneRun struct {
	Tag      string
	Dead     bool
	Status   int
	DeadTime int64 // unix seconds; 0 when tmux did not say
}

// signals are the numbers of the signals tmux names in pane_dead_signal; one not here reads as 128, still not a success.
var signals = map[string]int{"hup": 1, "int": 2, "quit": 3, "ill": 4, "trap": 5, "abrt": 6, "bus": 7, "fpe": 8, "kill": 9, "usr1": 10, "segv": 11,
	"usr2": 12, "pipe": 13, "alrm": 14, "term": 15}

// PaneRuns is every pane that carries a run tag, by handle; nil when tmux cannot say (a pane absent from a non-nil answer is gone).
func (t Tmux) PaneRuns() map[string]PaneRun {
	code, out, err := t.tmux(tmuxTimeout, "", "list-panes", "-a", "-F", "#{pane_id}\t#{"+RunOption+"}\t#{pane_dead}\t#{pane_dead_status}\t#{pane_dead_time}\t#{pane_dead_signal}")
	if code != 0 {
		low := strings.ToLower(err)
		for _, m := range noServer {
			if strings.Contains(low, m) {
				return map[string]PaneRun{}
			}
		}
		return nil
	}
	runs := map[string]PaneRun{}
	for _, line := range strings.Split(out, "\n") {
		f := strings.Split(line, "\t")
		if len(f) != 6 || !paneRE.MatchString(f[0]) || f[1] == "" {
			continue
		}
		r := PaneRun{Tag: f[1], Dead: f[2] == "1"}
		r.Status, _ = strconv.Atoi(f[3])
		r.DeadTime, _ = strconv.ParseInt(f[4], 10, 64)
		if f[5] != "" { // killed by a signal: tmux leaves the status empty and names the signal; a shell would say 128 plus its number
			r.Status = 128 + signals[strings.ToLower(f[5])]
		}
		runs[f[0]] = r
	}
	return runs
}

// PaneShell is the agent a shell pane was opened beside; "" for any other pane, known false when tmux cannot say.
func (t Tmux) PaneShell(handle string) (agent string, known bool) {
	return t.paneOption(handle, ShellOption)
}

// paneOption is the pane's value of a tmux pane option, known false when tmux cannot say.
func (t Tmux) paneOption(handle, option string) (string, bool) {
	if !paneRE.MatchString(handle) {
		return "", false
	}
	code, out, _ := t.tmux(tmuxTimeout, "", "display-message", "-p", "-t", handle, "#{"+option+"}")
	return strings.TrimSpace(out), code == 0
}

// newPane opens a window in cwd that runs shellCmd (the default shell when empty) and answers its pane's handle.
func (t Tmux) newPane(cwd, title, shellCmd string) (string, string) {
	var args []string
	if title != "" {
		args = append(args, "-n", windowNameRE.ReplaceAllString(title, "_"))
	}
	if cwd != "" {
		args = append(args, "-c", cwd)
	}
	// The daemon's home and its tmux server go into the window's environment under both spellings, so what runs there reads
	// them and not whatever the tmux server or the daemon's own environment was started with.
	if t.Home != "" {
		sock := paths.TmuxSocket(t.Home)
		args = append(args, "-e", "OCP_HOME="+t.Home, "-e", "HANDLINE_HOME="+t.Home, "-e", "OCP_TMUX_SOCKET="+sock, "-e", "HANDLINE_TMUX_SOCKET="+sock)
	}
	args = append(args, "-P", "-F", "#{pane_id}")
	if shellCmd != "" {
		args = append(args, shellCmd)
	}
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

// PaneAgent is the agent the daemon stamped the pane with; "" for a pane it did not open for an agent, known false when tmux cannot say.
func (t Tmux) PaneAgent(handle string) (agent string, known bool) {
	return t.paneOption(handle, AgentOption)
}

// Shells is what a host adds to open plain shells beside an agent.
type Shells interface {
	CreateShell(cwd, title, agent string) (handle, why string)
	PaneShell(handle string) (agent string, known bool)
}

func (d dynamic) CreateShell(cwd, title, agent string) (string, string) {
	if h, ok := d.cur().(Shells); ok {
		return h.CreateShell(cwd, title, agent)
	}
	return "", "the terminal host (" + d.Name() + ") cannot open a shell"
}

func (d dynamic) PaneShell(handle string) (string, bool) {
	if h, ok := d.cur().(Shells); ok {
		return h.PaneShell(handle)
	}
	return "", false
}

// Runs is what a host adds to run a command in a window of its own and read how it ended.
type Runs interface {
	CreateRun(cwd, title, agent, tag, command string) (handle, why string)
	PaneRuns() map[string]PaneRun
}

func (d dynamic) CreateRun(cwd, title, agent, tag, command string) (string, string) {
	if h, ok := d.cur().(Runs); ok {
		return h.CreateRun(cwd, title, agent, tag, command)
	}
	return "", "the terminal host (" + d.Name() + ") cannot run a command"
}

func (d dynamic) PaneRuns() map[string]PaneRun {
	if h, ok := d.cur().(Runs); ok {
		return h.PaneRuns()
	}
	return nil
}

type paneAgenter interface {
	PaneAgent(handle string) (string, bool)
}

func (d dynamic) PaneAgent(handle string) (string, bool) {
	if h, ok := d.cur().(paneAgenter); ok {
		return h.PaneAgent(handle)
	}
	return "", false
}

// PaneAgent is the agent the terminal was stamped for; known is false when the host cannot say.
func (t *Term) PaneAgent(handle string) (string, bool) {
	if h, ok := t.Backend.(paneAgenter); ok {
		return h.PaneAgent(handle)
	}
	return "", false
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

func (d dynamic) CreateTerminal(command, cwd, title, agent string) (string, string) {
	if h := d.host(); h != nil {
		return h.CreateTerminal(command, cwd, title, agent)
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
func (t *Term) CreateTerminal(command, cwd, title, agent string) (string, string) {
	if h := t.host(); h != nil {
		return h.CreateTerminal(command, cwd, title, agent)
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
	if blocked := t.DialogUp(handle); blocked != nil {
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
	// What reads the screen next (a startup dialog, the trust screen) must see the session drawn, not the shell it replaced.
	t.WaitDrawn(handle, DrawnTimeoutMs)
	return true, "relaunched"
}

// DrawnTimeoutMs is how long a launch waits for its session to be drawn.
const DrawnTimeoutMs = 30_000

// WaitDrawn is whether a session (its composer, a dialog, a running turn) or the trust screen is drawn at handle within timeoutMs. It
// does not wait for the session's first turn: a launch hands its prompt in a file, so nothing is typed behind it, and that turn can
// run for minutes.
func (t *Term) WaitDrawn(handle string, timeoutMs int) bool {
	deadline := t.now().Add(time.Duration(timeoutMs) * time.Millisecond)
	for {
		if session, _ := t.SessionOnScreen(handle); session || t.TrustPromptShowing(handle) {
			return true
		}
		if !t.now().Before(deadline) {
			return false
		}
		t.sleep(promptPoll)
	}
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

const (
	promptWait = 5 * time.Second
	promptPoll = 500 * time.Millisecond
)

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
