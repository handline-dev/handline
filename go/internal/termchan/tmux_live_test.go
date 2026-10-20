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
	// A plain shell opens in its folder, is listed by its stamp (the agent-less "cat" pane is not), and goes when closed.
	dir, _ := filepath.EvalSymlinks(t.TempDir())
	shell, why := tm.CreateShell(dir, "logs")
	if shell == "" {
		t.Fatal(why)
	}
	time.Sleep(300 * time.Millisecond)
	shells, ok := tm.Shells()
	if !ok || len(shells) != 1 || shells[0].Handle != shell || shells[0].Name != "logs" || shells[0].Cwd != dir || shells[0].Command == "" {
		t.Fatalf("shells %+v %v", shells, ok)
	}
	if ok, why := tm.CloseTerminal(shell, true); !ok {
		t.Fatal(why)
	}
	if shells, _ := tm.Shells(); len(shells) != 0 {
		t.Fatalf("a closed shell is listed: %+v", shells)
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
}
