// Package livetest is what the live tests share: they run only when HANDLINE_LIVE=1 asks for them, and spend the account
// tools/live_account.py picks, the one with the most usage headroom now. Nothing in tools/suite.sh, tools/check.sh or CI sets it.
package livetest

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

// Account is the account a live test spends, picked as tools/live_account.py says.
type Account struct {
	Name      string `json:"account"`
	Home      string `json:"home"` // the real handline home it is under
	ConfigDir string `json:"config_dir"`
	Why       string `json:"why"`
}

// Require skips the test unless HANDLINE_LIVE=1.
func Require(t *testing.T) {
	t.Helper()
	if os.Getenv("HANDLINE_LIVE") != "1" {
		t.Skip("a live test: HANDLINE_LIVE=1 runs it")
	}
}

// Pick skips the test unless HANDLINE_LIVE=1, else returns the account to spend and logs why it was picked. It sets a test
// root and HANDLINE_LIVE_ACCOUNTS for the test, so an account's config dir under a scratch home is the real one, where its login is.
func Pick(t *testing.T) Account {
	t.Helper()
	Require(t)
	_, here, _, _ := runtime.Caller(0)
	script := filepath.Join(filepath.Dir(here), "..", "..", "..", "tools", "live_account.py")
	out, err := exec.Command("python3", script).Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("live_account.py: %s", ee.Stderr)
		}
		t.Fatal(err)
	}
	var a Account
	if err := json.Unmarshal(out, &a); err != nil {
		t.Fatalf("live_account.py said %q: %v", out, err)
	}
	if root, _ := paths.Getenv("TEST_ROOT"); root == "" {
		t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	}
	t.Setenv("HANDLINE_"+team.LiveAccounts, a.Home)
	t.Logf("live account %s: %s", a.Name, a.Why)
	return a
}
