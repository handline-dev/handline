package paths

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A test that forgets to set a test root still never reaches the real home: the protected store, ~/.claude and the rest stay out of it.
func TestATestBinaryNeverResolvesTheRealUserHome(t *testing.T) {
	for _, k := range []string{"HANDLINE_TEST_ROOT", "OCP_TEST_ROOT"} {
		t.Setenv(k, "")
	}
	real, err := os.UserHomeDir()
	if err != nil {
		t.Skip("no user home to compare with")
	}
	got := UserHome()
	if got == "" || got == real || strings.HasPrefix(got, real+string(filepath.Separator)) {
		t.Fatalf("UserHome() = %q in a test binary, the real home is %q", got, real)
	}
	if got != UserHome() {
		t.Fatal("one test binary has one stand-in")
	}
}
