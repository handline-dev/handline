package team

import (
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/paths"
)

// A live run's scratch home takes its accounts' config dirs from HANDLINE_LIVE_ACCOUNTS, where the keychain keeps their logins.
func TestAccountConfigDirIsTheLiveAccountsOne(t *testing.T) {
	scratch, real := t.TempDir(), t.TempDir()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	t.Setenv("HANDLINE_"+LiveAccounts, "")
	got, err := AccountConfigDir(scratch, "theta")
	if err != nil || got != filepath.Join(paths.AccountDir(scratch, "theta"), ".claude") {
		t.Fatalf("unset: %q %v", got, err)
	}
	t.Setenv("HANDLINE_"+LiveAccounts, real)
	got, err = AccountConfigDir(scratch, "theta")
	if err != nil || got != filepath.Join(paths.AccountDir(real, "theta"), ".claude") {
		t.Fatalf("set: %q %v", got, err)
	}
}
