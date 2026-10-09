package hlclient

import (
	"os"
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
	t.Setenv("HOME", user)
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
	if why := LiveRefusal(a); !strings.Contains(why, "name different homes") || !strings.Contains(why, a) || !strings.Contains(why, b) {
		t.Fatalf("got %q", why)
	}
	t.Setenv("OCP_HOME", a+"/")
	if why := LiveRefusal(a); why != "" {
		t.Fatalf("one home spelled twice: %q", why)
	}
}
