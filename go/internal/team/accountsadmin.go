package team

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/backup"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// Why an account was not added.
var (
	ErrBadAccountName = errors.New("bad account name")
	ErrAccountExists  = errors.New("account exists")
)

// Add registers an account and links it to the shared files, as `hl account add --no-login`: its directory is made and nothing is logged
// in. Everything is checked before anything is made, and a failure while linking takes the new directory away again, so a refusal leaves
// no account behind. A name that is empty, holds a slash, starts with a dot or would not read back as text is ErrBadAccountName (wrapped,
// with the reason); a vendor with no driver is *UnknownVendor; a name in use is ErrAccountExists. Returns the config directory made.
func (a Accounts) Add(acct, vendor string) (string, error) {
	if acct == "" || strings.Contains(acct, "/") || strings.HasPrefix(acct, ".") {
		return "", fmt.Errorf("%w: %q", ErrBadAccountName, acct)
	}
	if !yamlcompat.IsPlainText(acct) {
		return "", fmt.Errorf("%w: %q. It would not be read back as text from the YAML files it is written into; choose another", ErrBadAccountName, acct)
	}
	if vendor == "" {
		vendor = DefaultVendor
	}
	if err := CheckVendor(vendor); err != nil {
		return "", err
	}
	dir := paths.AccountDir(a.Home, acct)
	if _, err := os.Lstat(dir); err == nil {
		return "", fmt.Errorf("%w: account '%s' already exists", ErrAccountExists, acct)
	}
	cdir := filepath.Join(dir, ConfigDirName(vendor))
	if err := os.MkdirAll(cdir, 0o755); err != nil {
		return "", err
	}
	fail := func(err error) (string, error) {
		os.RemoveAll(dir)
		return "", err
	}
	if vendor != DefaultVendor {
		if err := os.WriteFile(filepath.Join(dir, "vendor"), []byte(vendor+"\n"), 0o644); err != nil {
			return fail(err)
		}
	}
	shared := filepath.Join(a.Home, "shared", ConfigDirName(vendor))
	for _, item := range sharedItems(vendor) {
		target := filepath.Join(shared, item)
		if _, err := os.Stat(target); err != nil {
			continue // not made yet: the tick's relink adds it once it is
		}
		if err := os.Symlink(target, filepath.Join(cdir, item)); err != nil {
			return fail(fmt.Errorf("%s failed (%w); nothing was kept", item, err))
		}
	}
	return cdir, nil
}

// RemovePreview is what Remove would do to the account, line by line, and whether it would go ahead. It changes nothing: it names the backup
// gate's verdict as well as the account's own.
func (a Accounts) RemovePreview(q *sql.DB, acct string, now time.Time) (would bool, lines []string, err error) {
	ok, why, err := a.RemovePlan(q, acct)
	if err != nil {
		return false, nil, err
	}
	if !ok {
		return false, []string{"would refuse: " + why}, nil
	}
	lines = []string{fmt.Sprintf("would remove account '%s':", acct)}
	if argv, _ := a.LogoutArgv(acct); len(argv) > 0 {
		lines = append(lines, "  log it out with `"+strings.Join(argv, " ")+"`")
	} else {
		lines = append(lines, "  log it out: not logged in, nothing to do")
	}
	lines = append(lines, fmt.Sprintf("  move %s to %s/%s-<time> (kept, not deleted)", paths.AccountDir(a.Home, acct), filepath.Join(paths.AccountsDir(a.Home), ".retired"), acct))
	rows, err := q.Query("SELECT agent FROM agent_state WHERE account = ?", acct)
	if err != nil {
		return false, nil, err
	}
	var on []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			rows.Close()
			return false, nil, err
		}
		on = append(on, n)
	}
	rows.Close()
	if len(on) > 0 {
		lines = append(lines, fmt.Sprintf("  forget it on agent(s) %s (they start fresh on whatever account has room)", strings.Join(on, ", ")))
	} else {
		lines = append(lines, "  forget it on no agent (none is recorded on it)")
	}
	lines = append(lines, "  clear its usage polls and login check", "  "+backup.Describe(a.Home, now))
	gate := backup.RequireRecent(a.Home, now)
	if gate != "" {
		return false, append(lines, "would refuse: "+gate), nil
	}
	return true, append(lines, "backup gate: passed"), nil
}

// RemoveRefused is a removal that did not go ahead, and why; nothing was changed unless the message says a logout ran.
type RemoveRefused struct{ Why string }

func (e *RemoveRefused) Error() string { return e.Why }

// Remove logs the account out and takes it off the roster (accounts.remove_account), the confirmation having been asked already. run runs
// the vendor's logout and answers its exit code (-1 when it could not run). Refused while an agent is on the account, without a recent
// backup, and when the logout fails. The directory is moved aside, never deleted: the vendor removes its own credential, and moving the
// directory of a logged-in account would orphan it. Answers the line to say.
func (a Accounts) Remove(q *sql.DB, acct string, now time.Time, run func(argv []string, env map[string]string) int) (string, error) {
	ok, why, err := a.RemovePlan(q, acct)
	if err != nil {
		return "", err
	}
	if !ok {
		return "", &RemoveRefused{why}
	}
	if gate := backup.RequireRecent(a.Home, now); gate != "" {
		return "", &RemoveRefused{fmt.Sprintf("not removing '%s': %s", acct, gate)}
	}
	marker := filepath.Join(paths.AccountDir(a.Home, acct), ".removing") // nothing starts on an account about to go
	if err := os.WriteFile(marker, nil, 0o644); err != nil {
		return "", err
	}
	defer os.Remove(marker)
	if ok, why, err = a.RemovePlan(q, acct); err != nil {
		return "", err
	} else if !ok {
		return "", &RemoveRefused{why}
	}
	if argv, env := a.LogoutArgv(acct); len(argv) > 0 {
		if code := run(argv, env); code != 0 || a.LoggedIn(acct) {
			return "", &RemoveRefused{fmt.Sprintf("logout failed - '%s' is still logged in, so it stays. Run `%s` with %s=%s and try again.",
				acct, strings.Join(argv, " "), ConfigDirEnv(AccountVendor(a.Home, acct)), mustConfigDir(a.Home, acct))}
		}
	}
	os.Remove(marker)
	dest, err := a.Retire(acct, now)
	if err != nil {
		return "", err
	}
	// The name can be added again, and a poll remembered against the old account would hold the new one in its cooldown.
	for _, kind := range []string{"usage-poll", "usage-refresh", "login-check"} {
		if err := db.ClearCursor(q, kind+":"+acct); err != nil {
			return "", err
		}
	}
	// A closed agent's resume id lived under the directory just moved aside: it starts fresh on whatever account has room.
	if _, err := q.Exec("UPDATE agent_state SET account = NULL, account_source = NULL, resume_session = NULL WHERE account = ?", acct); err != nil {
		return "", err
	}
	return fmt.Sprintf("removed account '%s' (logged out; directory kept at %s)", acct, dest), nil
}

func mustConfigDir(home, acct string) string {
	d, _ := AccountConfigDir(home, acct)
	return d
}
