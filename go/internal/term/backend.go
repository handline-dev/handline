// Package term is how the daemon talks to the terminal an agent runs in (handline/session/term.py and backends/): a host that
// lists terminals, reads the rendered screen and types into one, and the readers above it that turn a screen into a dialog,
// a permission prompt or a question, and press keys on it only for what a fresh read just showed.
package term

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// Screen is a rendered frame: the bottom rows, top first, and where they came from. Anything but "screen" is the
// accumulated stream, whose history is the conversation and not what is drawn now.
type Screen struct {
	Tail   []string
	Source string
}

// Backend is everything the daemon asks of the program that hosts a terminal. Every method answers rather than panics, and
// "could not ask" is never "nothing there": ListTerminals and ReadScreen return nil when they could not answer.
type Backend interface {
	Name() string
	Available() bool
	ListTerminals() map[string]bool
	// ReadScreen is the bottom limit rows of the rendered frame.
	ReadScreen(handle string, limit int) *Screen
	// Send types text (raw bytes pass through), then Enter if asked.
	Send(handle, text string, enter bool) (bool, string)
	// WaitIdle is whether the terminal's program is quiet within timeoutMs; a timeout is false. turn is the vendor's reading of
	// whether a screen shows a turn in progress, for a host that must read the screen to tell.
	WaitIdle(handle string, timeoutMs int, turn func(tail []string) bool) bool
}

const (
	defaultBackend = "tmux"
	tmuxSocket     = "ocp"
	tmuxTimeout    = 15 * time.Second
	// Between typing a line and its Enter: a shell or TUI that gets both at once can take the Enter as part of a paste.
	enterDelay = 400 * time.Millisecond
	// How long the screen must hold still to count as idle, and how often it is looked at.
	idleSettle = 1500 * time.Millisecond
	idlePoll   = 500 * time.Millisecond
)

func getenv(suffix string) string {
	v, _ := paths.Getenv(suffix)
	return v
}

// run is one command as (exit code, stdout, stderr); a program that cannot run is (127, "", why). Stderr is clean at the source.
func run(timeout time.Duration, stdin string, name string, args ...string) (int, string, string) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode(), out.String(), textsafe.Oneline(strings.TrimSpace(errb.String()))
		}
		return 127, "", textsafe.Oneline(err.Error())
	}
	return 0, out.String(), textsafe.Oneline(strings.TrimSpace(errb.String()))
}

// ---- tmux: terminals are windows of one session on a server the daemon owns (tmux -L ocp)

var paneRE = regexp.MustCompile(`^%\d+$`)

// A lone terminal escape sequence is sent as the key tmux names, not as bytes tmux may deliver one at a time.
var tmuxKeys = map[string]string{"\x1b[A": "Up", "\x1b[B": "Down", "\x1b[C": "Right", "\x1b[D": "Left", "\x1b": "Escape", "\x03": "C-c"}

// What tmux says when nothing listens: not "error connecting" alone, which a socket the caller may not use says too.
var noServer = []string{"no server running", "no sessions", "can't find session", "(no such file or directory)", "(connection refused)"}

// Tmux drives panes through the tmux CLI.
type Tmux struct{}

func (Tmux) Name() string { return "tmux" }

func (Tmux) tmux(timeout time.Duration, stdin string, args ...string) (int, string, string) {
	sock := getenv("TMUX_SOCKET")
	if sock == "" {
		sock = tmuxSocket
	}
	return run(timeout, stdin, "tmux", append([]string{"-L", sock}, args...)...)
}

func (Tmux) Available() bool { _, err := exec.LookPath("tmux"); return err == nil }

func (t Tmux) ListTerminals() map[string]bool {
	code, out, err := t.tmux(tmuxTimeout, "", "list-panes", "-a", "-F", "#{pane_id}")
	if code == 0 {
		set := map[string]bool{}
		for _, p := range strings.Fields(out) {
			if paneRE.MatchString(p) {
				set[p] = true
			}
		}
		return set
	}
	low := strings.ToLower(err)
	for _, m := range noServer {
		if strings.Contains(low, m) {
			return map[string]bool{} // a server that is not running has no terminals: that is an answer
		}
	}
	return nil
}

// ReadScreen is the bottom limit rows of the pane. The rows below the last drawn one are dropped, so the bottom of Tail is the
// bottom of what is on screen. A limit taller than the pane reaches into its scrollback, which is how a reader widens its read
// when a dialog's top border has scrolled out of the frame.
func (t Tmux) ReadScreen(handle string, limit int) *Screen {
	if !paneRE.MatchString(handle) {
		return nil
	}
	code, out, _ := t.tmux(30*time.Second, "", "capture-pane", "-p", "-t", handle, "-S", "-"+strconv.Itoa(max(limit, 1)))
	if code != 0 {
		return nil
	}
	rows := strings.Split(out, "\n")
	for i := range rows {
		rows[i] = strings.TrimRightFunc(rows[i], func(r rune) bool { return r == ' ' || r == '\t' || r == '\r' || r == '\f' || r == '\v' })
	}
	for len(rows) > 0 && rows[len(rows)-1] == "" {
		rows = rows[:len(rows)-1]
	}
	if len(rows) > limit {
		rows = rows[len(rows)-limit:]
	}
	return &Screen{Tail: rows, Source: "screen"}
}

func (t Tmux) Send(handle, text string, enter bool) (bool, string) {
	if !paneRE.MatchString(handle) {
		return false, "not a tmux pane: " + pyRepr(handle)
	}
	if text != "" {
		var code int
		var err string
		switch {
		case tmuxKeys[text] != "":
			code, _, err = t.tmux(tmuxTimeout, "", "send-keys", "-t", handle, tmuxKeys[text])
		case strings.Contains(text, "\n"):
			// Several lines go as one bracketed paste, so a newline is text and not a submit.
			code, _, err = t.tmux(tmuxTimeout, text, "load-buffer", "-b", "ocp-send", "-")
			if code == 0 {
				code, _, err = t.tmux(tmuxTimeout, "", "paste-buffer", "-p", "-d", "-b", "ocp-send", "-t", handle)
			}
		default:
			code, _, err = t.tmux(tmuxTimeout, "", "send-keys", "-t", handle, "-l", "--", text)
		}
		if code != 0 {
			return false, err
		}
	}
	if enter {
		if text != "" {
			time.Sleep(enterDelay)
		}
		if code, _, err := t.tmux(tmuxTimeout, "", "send-keys", "-t", handle, "Enter"); code != 0 {
			return false, err
		}
	}
	return true, ""
}

// WaitIdle: the frame has not changed for idleSettle and turn (the vendor's reading) sees no turn in progress; with none
// given, a still frame is idle.
func (t Tmux) WaitIdle(handle string, timeoutMs int, turn func([]string) bool) bool {
	deadline := time.Now().Add(time.Duration(timeoutMs) * time.Millisecond)
	var last []string
	since := time.Now()
	for {
		scr := t.ReadScreen(handle, 40)
		if scr == nil {
			return false
		}
		now := time.Now()
		if last == nil || !equalRows(scr.Tail, last) {
			last, since = scr.Tail, now
		} else if now.Sub(since) >= idleSettle && !(turn != nil && turn(scr.Tail)) {
			return true
		}
		left := deadline.Sub(now)
		if left <= 0 {
			return false
		}
		time.Sleep(min(idlePoll, left))
	}
}

func equalRows(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// ---- which host

type unknown struct{ name string }

func (u unknown) Name() string                 { return u.name }
func (unknown) Available() bool                { return false }
func (unknown) ListTerminals() map[string]bool { return nil }
func (unknown) ReadScreen(string, int) *Screen { return nil }
func (u unknown) Send(string, string, bool) (bool, string) {
	return false, "unknown terminal backend " + pyRepr(u.name)
}
func (unknown) WaitIdle(string, int, func([]string) bool) bool { return false }

// ConfiguredName is the host in force: OCP_TERMINAL, else `terminal:` in config.yaml, else tmux. A name nobody knows gives a
// backend that is never available, so a typo stops the team with a message instead of quietly running on the other host.
func ConfiguredName(home string) string {
	if env := strings.ToLower(strings.TrimSpace(getenv("TERMINAL"))); env != "" {
		return env
	}
	text, err := os.ReadFile(paths.ConfigFile(home))
	if err != nil {
		return defaultBackend
	}
	data, err := yamlcompat.Parse(string(text))
	m, _ := data.(map[string]any)
	v, _ := m["terminal"].(string)
	if err != nil || v == "" {
		return defaultBackend
	}
	return strings.ToLower(strings.TrimSpace(v))
}

// Current is the backend in force for home.
func Current(home string) Backend {
	switch name := ConfiguredName(home); name {
	case "tmux":
		return Tmux{}
	default:
		return unknown{name}
	}
}

// dynamic is the backend in force, asked again on every call: the environment or config.yaml can change under a running daemon.
type dynamic struct{ home string }

// Dynamic is a Backend that is whatever Current(home) says each time it is used.
func Dynamic(home string) Backend { return dynamic{home} }

func (d dynamic) cur() Backend                                { return Current(d.home) }
func (d dynamic) Name() string                                { return d.cur().Name() }
func (d dynamic) Available() bool                             { return d.cur().Available() }
func (d dynamic) ListTerminals() map[string]bool              { return d.cur().ListTerminals() }
func (d dynamic) ReadScreen(h string, n int) *Screen          { return d.cur().ReadScreen(h, n) }
func (d dynamic) Send(h, t string, enter bool) (bool, string) { return d.cur().Send(h, t, enter) }
func (d dynamic) WaitIdle(h string, ms int, turn func([]string) bool) bool {
	return d.cur().WaitIdle(h, ms, turn)
}
