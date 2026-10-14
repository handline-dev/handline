package paths

import (
	"os"
	"path/filepath"
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
