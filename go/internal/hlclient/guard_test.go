package hlclient

import (
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"testing"
)

// liveEnv is a real-user home holding .handline, with no test root, no ALLOW_LIVE and the homes unset.
func liveEnv(t *testing.T) (live string) {
	t.Helper()
	user := t.TempDir()
	live = filepath.Join(user, ".handline")
	if err := os.MkdirAll(live, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", t.TempDir()) // never where the guard looks: that is the user database's
	old := realUserHome
	realUserHome = func() string { return user }
	t.Cleanup(func() { realUserHome = old })
	for _, k := range []string{"HANDLINE_TEST_ROOT", "OCP_TEST_ROOT", "HANDLINE_ALLOW_LIVE", "OCP_ALLOW_LIVE", "HANDLINE_HOME", "OCP_HOME",
		"HANDLINE_BIN", "OCP_BIN"} {
		t.Setenv(k, "")
	}
	return live
}

func TestAnotherCheckoutIsRefusedTheLiveHome(t *testing.T) {
	live := liveEnv(t)
	t.Setenv("HANDLINE_BIN", filepath.Join(t.TempDir(), "clone", "bin", "hl"))
	why := LiveRefusal(live)
	if !strings.Contains(why, "not the checkout the installed hl runs") || !strings.Contains(why, "HANDLINE_ALLOW_LIVE=1") {
		t.Fatalf("live: %q", why)
	}
	if LiveRefusal(filepath.Join(live, "sub")) == "" {
		t.Fatal("a folder inside the live home is the live home")
	}
	if why := LiveRefusal(t.TempDir()); why != "" {
		t.Fatalf("any other home: %q", why)
	}
	t.Setenv("HANDLINE_ALLOW_LIVE", "1")
	if why := LiveRefusal(live); why != "" {
		t.Fatalf("allowed: %q", why)
	}
}

func TestTheInstalledCheckoutReachesTheLiveHome(t *testing.T) {
	live := liveEnv(t)
	here, _ := checkouts()
	t.Setenv("HANDLINE_BIN", filepath.Join(here, "bin", "hl"))
	if why := LiveRefusal(live); why != "" {
		t.Fatalf("installed: %q", why)
	}
}

func TestATestReachingTheLiveHomeIsRefusedEvenWhenAllowed(t *testing.T) {
	live := liveEnv(t)
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	t.Setenv("HANDLINE_ALLOW_LIVE", "1")
	if why := LiveRefusal(live); !strings.HasPrefix(why, "a test reached the real home") {
		t.Fatalf("got %q", why)
	}
}

func TestTwoNamesForTwoHomesAreRefused(t *testing.T) {
	liveEnv(t)
	a, b := t.TempDir(), t.TempDir()
	t.Setenv("HANDLINE_HOME", a)
	t.Setenv("OCP_HOME", b)
	if why := LiveRefusal(a); why != "HANDLINE_HOME="+a+" and OCP_HOME="+b+" name different homes: set one, or both to the same folder" {
		t.Fatalf("got %q", why)
	}
	t.Setenv("OCP_HOME", " ")
	if why := LiveRefusal(a); why != "" {
		t.Fatalf("a blank second name: %q", why)
	}
	t.Setenv("OCP_HOME", a+"/")
	if why := LiveRefusal(a); why != "" {
		t.Fatalf("one home spelled twice: %q", why)
	}
}

func TestTheRealHomeComesFromTheUserDatabaseNotHOME(t *testing.T) {
	u, err := user.Current()
	if err != nil {
		t.Skip("no user database here:", err)
	}
	t.Setenv("HOME", t.TempDir())
	if got := realUserHome(); got != u.HomeDir {
		t.Fatalf("with HOME moved: %q, want %q", got, u.HomeDir)
	}
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	if why := LiveRefusal(filepath.Join(u.HomeDir, ".handline")); !strings.HasPrefix(why, "a test reached the real home") {
		t.Fatalf("a test with HOME moved still reaches the real home: %q", why)
	}
}
