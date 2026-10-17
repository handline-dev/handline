package term

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeTmux puts a `tmux` on PATH that logs its arguments, one call a line, and answers like a server: has-session fails until
// new-session has run, and new-window and new-session answer pane %7.
func fakeTmux(t *testing.T) (log string) {
	t.Helper()
	dir := t.TempDir()
	log = filepath.Join(dir, "calls")
	script := `#!/bin/sh
printf '%s\n' "$*" >> ` + log + `
case "$3" in
  has-session) [ -f ` + dir + `/up ] && exit 0; echo "no server running on x" >&2; exit 1;;
  new-session) touch ` + dir + `/up; echo %7;;
  new-window) echo %7;;
esac
exit 0
`
	if err := os.WriteFile(filepath.Join(dir, "tmux"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+":"+os.Getenv("PATH"))
	t.Setenv("OCP_TMUX_SOCKET", "t")
	t.Setenv("OCP_HOME", "/h")
	t.Setenv("HANDLINE_HOME", "/h")
	return log
}

func calls(t *testing.T, log string) []string {
	b, _ := os.ReadFile(log)
	return strings.Split(strings.TrimSpace(string(b)), "\n")
}

func TestATerminalIsOpenedInTheWorktreeAndTheLaunchLineTypedAtItsShell(t *testing.T) {
	log := fakeTmux(t)
	handle, why := Tmux{"/h"}.CreateTerminal("hl launch alice", "/wt/alice", "alice.x")
	if handle != "%7" || why != "" {
		t.Fatalf("%q %q", handle, why)
	}
	got := calls(t, log)
	// no server yet: a session is made with a size; the window name has no dots or colons; the home goes in the environment
	want := "-L t new-session -d -s ocp -x 200 -y 50 -n alice_x -c /wt/alice -e OCP_HOME=/h -e HANDLINE_HOME=/h -e OCP_TMUX_SOCKET=t -e HANDLINE_TMUX_SOCKET=t -P -F #{pane_id}"
	if got[1] != want {
		t.Fatalf("%q", got)
	}
	if got[len(got)-2] != "-L t send-keys -t %7 -l -- hl launch alice" || got[len(got)-1] != "-L t send-keys -t %7 Enter" {
		t.Fatalf("%q", got)
	}
	// a second terminal is a window of the session that is up
	Tmux{"/h"}.CreateTerminal("hl launch bob", "/wt/bob", "bob")
	got = calls(t, log)
	if !strings.HasPrefix(got[len(got)-3], "-L t new-window -t ocp: -n bob -c /wt/bob") {
		t.Fatalf("%q", got)
	}
}

func TestAWindowWhoseLaunchLineCouldNotBeTypedIsClosedAgain(t *testing.T) {
	log := fakeTmux(t)
	dir := filepath.Dir(log)
	os.WriteFile(filepath.Join(dir, "tmux"), []byte("#!/bin/sh\nprintf '%s\\n' \"$*\" >> "+log+"\ncase \"$3\" in has-session) exit 1;; new-session) echo %7;; send-keys) echo no pane >&2; exit 1;; esac\n"), 0o755)
	handle, why := Tmux{"/h"}.CreateTerminal("hl launch alice", "", "")
	if handle != "" || !strings.Contains(why, "could not type the command") {
		t.Fatalf("%q %q", handle, why)
	}
	if got := calls(t, log); !strings.HasSuffix(got[len(got)-1], "kill-window -t %7") {
		t.Fatalf("%q", got)
	}
	if ok, _ := (Tmux{}).CloseTerminal("not a pane", true); ok {
		t.Fatal("only a pane id is closed")
	}
}

// hostBackend is a backend with a host: it shows a screen, lists its terminals and records what is typed and respawned.
type hostBackend struct {
	mu      sync.Mutex
	screen  func(sent int) []string
	live    map[string]bool
	sent    []string
	respawn []string
	closed  []string
	waited  int
}

func (h *hostBackend) Name() string                                   { return "fake" }
func (h *hostBackend) Available() bool                                { return true }
func (h *hostBackend) ListTerminals() map[string]bool                 { return h.live }
func (h *hostBackend) WaitIdle(string, int, func([]string) bool) bool { h.waited++; return true }
func (h *hostBackend) ReadScreen(string, int) *Screen {
	h.mu.Lock()
	defer h.mu.Unlock()
	return &Screen{Tail: h.screen(len(h.sent)), Source: "screen"}
}
func (h *hostBackend) Send(_, text string, enter bool) (bool, string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.sent = append(h.sent, fmt.Sprintf("%s/%v", text, enter))
	return true, ""
}
func (h *hostBackend) CreateTerminal(string, string, string) (string, string) { return "%1", "" }
func (h *hostBackend) CloseTerminal(handle string, _ bool) (bool, string) {
	h.closed = append(h.closed, handle)
	return true, ""
}
func (h *hostBackend) RespawnTerminal(handle string) (bool, string) {
	h.respawn = append(h.respawn, handle)
	return true, ""
}

func hostTerm(h *hostBackend) *Term {
	t := New(h, nil)
	t.Settle = 0
	var now time.Time
	t.Now = func() time.Time { return now }
	t.Sleep = func(d time.Duration) { now = now.Add(d) }
	return t
}

var composer = []string{rule, "│ > │", rule, "  ? for shortcuts"}

func TestASessionIsEndedByRestartingItsShellNotByTypingAtIt(t *testing.T) {
	h := &hostBackend{live: map[string]bool{"%1": true}, screen: func(int) []string { return composer }}
	ok, why := hostTerm(h).CloseSession("%1")
	if !ok || len(h.sent) != 0 || len(h.respawn) != 1 || h.respawn[0] != "%1" {
		t.Fatalf("%v %q sent %q respawn %q", ok, why, h.sent, h.respawn)
	}
	// a terminal that is gone, and a dialog standing on the screen, are left alone
	h = &hostBackend{live: map[string]bool{}, screen: func(int) []string { return composer }}
	if ok, why := hostTerm(h).CloseSession("%1"); ok || !strings.Contains(why, "not alive") || len(h.respawn) != 0 {
		t.Fatalf("%v %q", ok, why)
	}
	h = &hostBackend{live: map[string]bool{"%1": true}, screen: func(int) []string { return permissionPrompt }}
	if ok, why := hostTerm(h).CloseSession("%1"); ok || len(h.respawn) != 0 || len(h.sent) != 0 {
		t.Fatalf("a modal was ended under: %v %q", ok, why)
	}
}

func TestARelaunchTypesTheLineThenItsEnterAndAsksWhetherAnythingStarted(t *testing.T) {
	shell := []string{"~/work $ "}
	h := &hostBackend{live: map[string]bool{"%1": true}, screen: func(int) []string { return shell }}
	ok, why := hostTerm(h).Relaunch("%1", "hl launch alice", func() bool { return true })
	if !ok || strings.Join(h.sent, ",") != "hl launch alice/false,/true" {
		t.Fatalf("%v %q %q", ok, why, h.sent)
	}
	h = &hostBackend{live: map[string]bool{"%1": true}, screen: func(int) []string { return shell }}
	ok, why = hostTerm(h).Relaunch("%1", "hl launch alice", func() bool { return false })
	if ok || !strings.Contains(why, "no session started") || !strings.Contains(why, "the terminal is alive (screen: ~/work $)") {
		t.Fatalf("%v %q", ok, why)
	}
	// the launch is done once its session is drawn: a first turn that keeps the screen busy is not waited out
	turn := append([]string{"✻ Working… (esc to interrupt)"}, composer...)
	h = &hostBackend{live: map[string]bool{"%1": true}, screen: func(n int) []string {
		if n < 2 {
			return shell
		}
		return turn
	}}
	if ok, why := hostTerm(h).Relaunch("%1", "hl launch alice", func() bool { return true }); !ok || h.waited != 0 {
		t.Fatalf("%v %q, waited for idle %d times", ok, why, h.waited)
	}
	// a session already drawn there is not typed at
	h = &hostBackend{live: map[string]bool{"%1": true}, screen: func(int) []string { return composer }}
	if ok, why := hostTerm(h).Relaunch("%1", "hl launch alice", nil); ok || len(h.sent) != 0 || !strings.Contains(why, "a session is on the terminal's screen") {
		t.Fatalf("%v %q %q", ok, why, h.sent)
	}
}

func TestWaitingForTheTrustPromptToGoGivesUpAfterFiveSeconds(t *testing.T) {
	trust := []string{"Quick safety check: Is this a project you created or one you trust?"}
	h := &hostBackend{live: map[string]bool{"%1": true}, screen: func(n int) []string {
		if n >= 0 {
			return trust
		}
		return composer
	}}
	if hostTerm(h).WaitForTrustPrompt("%1", false) {
		t.Fatal("it never went")
	}
	if !hostTerm(h).WaitForTrustPrompt("%1", true) {
		t.Fatal("it was showing")
	}
}

func TestOnlyTmuxIsAHostAndAnyOtherNameIsNeverAvailable(t *testing.T) {
	t.Setenv("HANDLINE_TERMINAL", "")
	t.Setenv("OCP_TERMINAL", "")
	home := t.TempDir()
	if b := Current(home); b.Name() != "tmux" {
		t.Fatalf("with nothing said: %q", b.Name())
	}
	os.WriteFile(filepath.Join(home, "config.yaml"), []byte("terminal: orca\n"), 0o644)
	b := Current(home)
	if ok, why := b.Send("h", "x", true); b.Name() != "orca" || b.Available() || b.ListTerminals() != nil || ok || !strings.Contains(why, "orca") {
		t.Fatalf("a host the daemon does not drive: %q %v %q", b.Name(), b.Available(), why)
	}
}

// A daemon on a scratch home never reaches the live tmux server (-L ocp), whether the socket setting is missing or names ocp: a
// scratch daemon that did put a window among the live agents'.
func TestAScratchDaemonNeverReachesTheLiveTmuxServer(t *testing.T) {
	log := fakeTmux(t)
	home := t.TempDir()
	for _, sock := range []string{"", "ocp"} {
		t.Setenv("HANDLINE_TMUX_SOCKET", sock)
		t.Setenv("OCP_TMUX_SOCKET", sock)
		tm := Current(home).(Tmux)
		tm.ListTerminals()
		tm.CreateTerminal("hl launch alice", home, "alice")
		for _, argv := range [][]string{tm.ControlCommand(), tm.AttachCommand()} {
			if argv[1] != "-L" || argv[2] == "ocp" {
				t.Errorf("socket %q: %q", sock, argv)
			}
		}
	}
	for _, line := range calls(t, log) {
		if !strings.HasPrefix(line, "-L ocp-") {
			t.Errorf("not on the scratch home's own server: %q", line)
		}
	}
}

// A terminal names the daemon's own home and tmux server, not the live home a daemon's environment may still carry: what runs in a
// scratch daemon's window must not reach the live daemon.
func TestATerminalNamesTheDaemonsHomeNotTheEnvironments(t *testing.T) {
	log := fakeTmux(t)
	live, home := filepath.Join(t.TempDir(), ".handline"), t.TempDir()
	t.Setenv("OCP_HOME", live)
	t.Setenv("HANDLINE_HOME", live)
	t.Setenv("OCP_TMUX_SOCKET", "")
	t.Setenv("HANDLINE_TMUX_SOCKET", "")
	if _, why := Current(home).(Tmux).CreateTerminal("hl launch alice", home, "alice"); why != "" {
		t.Fatal(why)
	}
	made := calls(t, log)[1]
	sock := strings.Fields(made)[1]
	for _, want := range []string{"OCP_HOME=" + home, "HANDLINE_HOME=" + home, "OCP_TMUX_SOCKET=" + sock, "HANDLINE_TMUX_SOCKET=" + sock} {
		if !strings.Contains(made, " -e "+want+" ") {
			t.Errorf("no %s in %q", want, made)
		}
	}
	if strings.Contains(made, live) {
		t.Errorf("the environment's home reached the window: %q", made)
	}
}
