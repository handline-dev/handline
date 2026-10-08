package lifecycle

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/paths"
)

const (
	RecoverModel = "haiku"
	// RecoverPrompt is one line nothing downstream has to think about: the turn exists to be spent, not read.
	RecoverPrompt = "hi"
)

// Reading is a usage reading of an account as recover needs it.
type Reading interface{}

// RecoverPorts is what one recovery attempt reaches in the other groups (usage, vendors, accounts, fold).
type RecoverPorts interface {
	// UsageRecoverEnabled is load.usage_recover_enabled: whether #1885 is turned on for the account.
	UsageRecoverEnabled(acct string) bool
	// RenewsByTurn is whether the account's vendor reads usage and a real turn renews its credential; err is an unknown vendor, and
	// name is the vendor's name for the refusal.
	RenewsByTurn(acct string) (renews bool, name string, err error)
	LoggedIn(acct string) bool
	// LongTrusted is the account's longest-window figure, the floor a stale reading carries; ok false when truly unknown. label names the window.
	LongTrusted(acct string) (pct int, label string, ok bool)
	LongBlock() int
	// RunModel is fold.run_model: one real turn on the account's own credential.
	RunModel(prompt, acct, model string) (ok bool, out string)
	// ReadUsage is usage_api.read_detailed with no refresh: the reading, or whether the failure was an auth refusal (401/403).
	ReadUsage(acct string) (reading Reading, authFailed bool)
	Record(reading Reading)
	Describe(reading Reading) string
}

// Recoverer is usage/recover.py: a stale or dead account earns itself back with one real turn. It is the mechanism only; noticing that
// a call is worth making belongs where the poll runs.
type Recoverer struct {
	Home  string
	Ports RecoverPorts
}

func (r *Recoverer) reloginFile(acct string) string {
	return filepath.Join(paths.AccountDir(r.Home, acct), "needs_relogin")
}

// NeedsRelogin is set once a real turn still left the credential refused: the one case nothing left to try would clear, so Recover
// keeps refusing rather than retrying for ever.
func (r *Recoverer) NeedsRelogin(acct string) bool {
	_, err := os.Stat(r.reloginFile(acct))
	return err == nil
}

// MarkRelogin records why the account needs a person.
func (r *Recoverer) MarkRelogin(acct, why string) error {
	p := r.reloginFile(acct)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	return os.WriteFile(p, []byte(why+"\n"), 0o644)
}

// ClearRelogin removes the mark; none is not an error.
func (r *Recoverer) ClearRelogin(acct string) {
	_ = os.Remove(r.reloginFile(acct))
}

// Recover spends one real turn on acct and re-reads it. It refuses before spending anything: an account already marked, one #1885
// is off for, one no vendor here reads usage for, one not logged in, and one at its longest window's wall.
func (r *Recoverer) Recover(acct string) (bool, string) {
	p := r.Ports
	if r.NeedsRelogin(acct) {
		return false, acct + " needs re-login - not retrying"
	}
	if !p.UsageRecoverEnabled(acct) {
		return false, acct + ": usage recovery is turned off"
	}
	renews, name, err := p.RenewsByTurn(acct)
	if err != nil {
		return false, fmt.Sprintf("%s: %v", acct, err)
	}
	if !renews {
		return false, fmt.Sprintf("%s is a %s account - no turn renews its credential", acct, name)
	}
	if !p.LoggedIn(acct) {
		return false, acct + " is not logged in"
	}
	// The longest-window figure is carried as a floor until its reset passes, so this still catches an account genuinely at the wall
	// even though the module exists for a stale reading. When the figure is truly unknown a turn is spent: that is the account #1885
	// exists to unstick.
	if week, label, ok := p.LongTrusted(acct); ok && week >= p.LongBlock() {
		return false, fmt.Sprintf("%s: %s %d%% is at the wall - not spending a turn on it", acct, label, week)
	}
	if ok, out := p.RunModel(RecoverPrompt, acct, RecoverModel); !ok {
		return false, fmt.Sprintf("%s: recovery turn failed: %s", acct, out)
	}
	reading, authFailed := p.ReadUsage(acct)
	if reading == nil {
		if authFailed {
			if err := r.MarkRelogin(acct, "a real turn ran but the credential was still refused (401/403)"); err != nil {
				return false, fmt.Sprintf("%s: still unreadable after a real turn, and could not mark it needs re-login: %v", acct, err)
			}
			return false, acct + ": still unreadable after a real turn - marked needs re-login"
		}
		return false, acct + ": still unreadable after a real turn - not marked, may be transient"
	}
	r.ClearRelogin(acct)
	p.Record(reading)
	return true, fmt.Sprintf("%s: recovered - %s", acct, p.Describe(reading))
}
