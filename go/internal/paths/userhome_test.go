package paths

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A test that forgets to set a test root still never reaches the real home (the protected store, ~/.claude and the rest stay out of
// it), and leaves no folder behind either.
func TestATestBinaryNeverResolvesTheRealUserHome(t *testing.T) {
	for _, k := range []string{"HANDLINE_TEST_ROOT", "OCP_TEST_ROOT"} {
		t.Setenv(k, "")
	}
	real, err := os.UserHomeDir()
	if err != nil {
		t.Skip("no user home to compare with")
	}
	got := UserHome()
	// not "outside the real home": go test builds under the temp folder, which a Linux CI runner keeps inside it
	if got == "" || got == real {
		t.Fatalf("UserHome() = %q in a test binary, the real home is %q", got, real)
	}
	if got != UserHome() {
		t.Fatal("one test binary has one stand-in")
	}
	// beside the binary: `go test` builds it in a folder it removes after the run, so the stand-in goes with it
	if exe, _ := os.Executable(); filepath.Dir(got) != filepath.Dir(exe) {
		t.Fatalf("the stand-in %q is not beside the test binary %q", got, exe)
	}
}

// The live home's tmux server is ocp unless TMUX_SOCKET says otherwise; any other home gets one of its own, the same each time,
// and never ocp.
func TestTheTmuxServerFollowsTheHome(t *testing.T) {
	user := t.TempDir()
	old := RealUserHome
	RealUserHome = func() string { return user }
	t.Cleanup(func() { RealUserHome = old })
	live, a, b := filepath.Join(user, ".handline"), t.TempDir(), t.TempDir()
	for _, c := range []struct{ sock, home, want string }{
		{"", live, "ocp"},
		{"mine", live, "mine"},
		{"mine", a, "mine"},
	} {
		t.Setenv("HANDLINE_TMUX_SOCKET", c.sock)
		if got := TmuxSocket(c.home); got != c.want {
			t.Errorf("%+v: %q", c, got)
		}
	}
	for _, sock := range []string{"", "ocp"} {
		t.Setenv("HANDLINE_TMUX_SOCKET", sock)
		got := TmuxSocket(a)
		if !strings.HasPrefix(got, "ocp-") || got != TmuxSocket(a) || got == TmuxSocket(b) {
			t.Errorf("socket %q: %q (other home %q)", sock, got, TmuxSocket(b))
		}
	}
}
