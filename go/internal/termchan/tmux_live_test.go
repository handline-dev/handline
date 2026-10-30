package termchan

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/testkill"
)

// A real tmux server, when one can be started here: the host methods and the control link against the program they are for. Once the
// test ends, pass or fail, neither the server nor its socket file is left.
func TestLiveTmux(t *testing.T) {
	if _, err := exec.LookPath("tmux"); err != nil {
		t.Skip("no tmux")
	}
	sock := "ocptest" + strings.ReplaceAll(t.Name(), "/", "") + time.Now().Format("150405")
	var file string
	if !t.Run("server", func(t *testing.T) { liveTmux(t, sock, &file) }) || file == "" {
		return
	}
	if _, err := os.Stat(file); !os.IsNotExist(err) {
		t.Fatalf("the socket file %s is left behind (%v)", file, err)
	}
}

func liveTmux(t *testing.T, sock string, file *string) {
	t.Setenv("HANDLINE_TMUX_SOCKET", sock)
	t.Setenv("SHELL", "/bin/sh") // the server and its windows start with this one: no user rc files to wait for
	run := func(args ...string) string {
		out, err := exec.Command("tmux", append([]string{"-L", sock}, args...)...).CombinedOutput()
		if err != nil {
			t.Skipf("tmux cannot run here: %v %s", err, out)
		}
		return strings.TrimSpace(string(out))
	}
	run("new-session", "-d", "-s", "ocp", "-x", "60", "-y", "10", "cat")
	*file = run("display-message", "-p", "#{socket_path}")
	testkill.OnEnd(t, func() { // the server leaves our group: it is killed by name, and tmux leaves its socket file
		exec.Command("tmux", "-L", sock, "kill-server").Run()
		os.Remove(*file)
	})
	pane := run("list-panes", "-a", "-F", "#{pane_id}")
	tm := term.Tmux{}
	if ok, why := tm.SendBytes(pane, []byte("hello \x1b[1mworld\x1b[0m\r")); !ok {
		t.Fatal(why)
	}
	time.Sleep(300 * time.Millisecond)
	if c, r, ok := tm.Size(pane); !ok || c != 60 || r != 10 {
		t.Fatalf("size %d %d %v", c, r, ok)
	}
	if cur := tm.Cursor(pane); cur == nil || cur.Height != 10 || !cur.Visible {
		t.Fatalf("cursor %+v", cur)
	}
	if rows := tm.ReadScreenANSI(pane, 10); len(rows) == 0 || !strings.Contains(strings.Join(rows, "\n"), "hello") {
		t.Fatalf("rows %q", rows)
	}
	if ok, why := tm.Resize(pane, 40, 8); !ok {
		t.Logf("resize: %s", why)
	}
	l := NewControlLink(SpawnCommand(tm.ControlCommand()), true)
	if !l.Start() {
		t.Fatal("control link")
	}
	defer l.Stop()
	got := make(chan *Snapshot, 1)
	Capture(l, pane, 5, func(s *Snapshot) { got <- s }, nil)
	select {
	case s := <-got:
		if s == nil || !strings.Contains(string(s.Data), "hello") {
			t.Fatalf("snapshot %+v", s)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("no snapshot")
	}

	// A shell: the user's login shell in the given directory, stamped as a shell of the agent, closed on its own.
	dir := t.TempDir()
	sh, why := tm.CreateShell(dir, "ann-sh", "ann")
	if sh == "" {
		t.Fatal(why)
	}
	if a, known := tm.PaneShell(sh); a != "ann" || !known {
		t.Fatalf("shell stamp %q %v", a, known)
	}
	if a, _ := tm.PaneShell(pane); a != "" {
		t.Fatalf("another pane is no shell: %q", a)
	}
	if ok, why := tm.SendBytes(sh, []byte("pwd -P\r")); !ok {
		t.Fatal(why)
	}
	want, _ := filepath.EvalSymlinks(dir)
	deadline := time.Now().Add(15 * time.Second)
	for !strings.Contains(strings.Join(tm.ReadScreen(sh, 10).Tail, ""), want) {
		if time.Now().After(deadline) {
			t.Fatalf("no %s in the shell's screen: %q", want, tm.ReadScreen(sh, 10).Tail)
		}
		time.Sleep(100 * time.Millisecond)
	}
	if ok, why := tm.CloseTerminal(sh, true); !ok || tm.ListTerminals()[sh] {
		t.Fatalf("shell not closed: %v %s", ok, why)
	}

	// A command: its window stays after it ends, with tmux's own exit status and end time; a window closed first is no longer there.
	for _, c := range []struct {
		command string
		dead    bool
		status  int
	}{{"true", true, 0}, {"exit 3", true, 3}, {"sleep 60", false, 0}} {
		run, why := tm.CreateRun(dir, "ann-run", "ann", "9:0@1", c.command)
		if run == "" {
			t.Fatalf("%s: %s", c.command, why)
		}
		if a, _ := tm.PaneShell(run); a != "ann" {
			t.Fatalf("a run window is a shell of the agent: %q", a)
		}
		deadline := time.Now().Add(15 * time.Second)
		for {
			got, here := tm.PaneRuns()[run]
			if here && got.Tag == "9:0@1" && got.Dead == c.dead && got.Status == c.status && (!c.dead || got.DeadTime > 0) {
				break
			}
			if time.Now().After(deadline) {
				t.Fatalf("%s: tmux says %+v", c.command, got)
			}
			time.Sleep(50 * time.Millisecond)
		}
		if ok, why := tm.CloseTerminal(run, true); !ok {
			t.Fatal(why)
		}
		if _, here := tm.PaneRuns()[run]; here {
			t.Fatalf("%s: a closed window is still listed", c.command)
		}
	}
}
