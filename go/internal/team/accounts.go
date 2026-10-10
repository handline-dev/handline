package team

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/paths"
)

// Accounts is handline/team/accounts.py: the account directories and what is known of their logins. Credentials are never
// touched: a login is told by a credential's presence (and, for the keychain, its modification time), never by reading it.
// Registering an account's shared links, the subscription and identity lookups through the vendor's own commands, and
// adding or removing an account are not here.
type Accounts struct{ Home string }

// ModifiedTTLS: the keychain read behind a credential's modification time is a subprocess, and every tick asks for each agent on
// a marked account, so a login is seen within this long.
const ModifiedTTLS = 5

// modifiedKey is one account of one home: two homes in a process (a test's, or the daemon's and a probe's) never share a stamp.
type modifiedKey struct{ home, acct string }

var (
	modifiedMu sync.Mutex
	modified   = map[modifiedKey]struct {
		at    time.Time
		stamp *float64
	}{}
	mdatRe = regexp.MustCompile(`"mdat"<timedate>=\S+\s+"(\d{14})Z`)
)

func (a Accounts) expiredFile(acct string) string {
	return filepath.Join(paths.AccountDir(a.Home, acct), "login_expired.json")
}

// credentialModified is when the credential last changed, as epoch seconds, never its content; nil when this vendor or process cannot tell.
func (a Accounts) credentialModified(acct string) *float64 {
	modifiedMu.Lock()
	seen, ok := modified[modifiedKey{a.Home, acct}]
	modifiedMu.Unlock()
	if ok && time.Since(seen.at) < ModifiedTTLS*time.Second {
		return seen.stamp
	}
	var stamp *float64
	if dir, err := AccountConfigDir(a.Home, acct); err == nil && AccountVendor(a.Home, acct) == DefaultVendor {
		stamp = claudeCredentialModified(dir)
	}
	modifiedMu.Lock()
	modified[modifiedKey{a.Home, acct}] = struct {
		at    time.Time
		stamp *float64
	}{time.Now(), stamp}
	modifiedMu.Unlock()
	return stamp
}

func claudeCredentialModified(configDir string) *float64 {
	if st, err := os.Stat(filepath.Join(configDir, ".credentials.json")); err == nil {
		v := float64(st.ModTime().UnixNano()) / 1e9
		return &v
	}
	if !isDarwin() {
		return nil
	}
	rc, out := run(10*time.Second, nil, "security", "find-generic-password", "-s", KeychainService(configDir))
	_ = rc
	m := mdatRe.FindStringSubmatch(out)
	if m == nil {
		return nil
	}
	t, err := time.ParseInLocation("20060102150405", m[1], time.UTC)
	if err != nil {
		return nil
	}
	v := float64(t.Unix())
	return &v
}

// Expired is why and when an account was found with an expired login, until a login after that time.
type Expired struct {
	Why string
	At  float64
}

// LoginExpired is the mark, when there is one. The credential is still there, so its presence says nothing; its modification
// time does: a login writes it anew, and the mark then goes. A vendor that cannot give the time keeps the mark until cleared.
func (a Accounts) LoginExpired(acct string) *Expired {
	b, err := os.ReadFile(a.expiredFile(acct))
	if err != nil {
		return nil
	}
	var data struct {
		Why any      `json:"why"`
		At  *float64 `json:"at"`
	}
	if json.Unmarshal(b, &data) != nil || data.At == nil {
		return nil
	}
	if changed := a.credentialModified(acct); changed != nil && *changed > *data.At {
		a.ClearLoginExpired(acct)
		return nil
	}
	return &Expired{Why: pyStr(data.Why), At: *data.At}
}

// MarkLoginExpired records that an account's login expired. False when it already is marked: the first sighting stands.
func (a Accounts) MarkLoginExpired(acct, why string, now time.Time) (bool, error) {
	if a.LoginExpired(acct) != nil {
		return false, nil
	}
	f := a.expiredFile(acct)
	if err := os.MkdirAll(filepath.Dir(f), 0o755); err != nil {
		return false, err
	}
	whyJSON, _ := json.Marshal(why)
	text := fmt.Sprintf(`{"why": %s, "at": %s}`, whyJSON, yamlFloat(float64(now.UnixNano())/1e9))
	return true, os.WriteFile(f, []byte(text), 0o644)
}

// ClearLoginExpired drops the mark.
func (a Accounts) ClearLoginExpired(acct string) { _ = os.Remove(a.expiredFile(acct)) }

// LoginState is whether the account has credentials, as its driver answers: a file's existence for claude, a run of `opencode
// auth list` for opencode. An account whose vendor has no driver cannot be driven, so it is not logged in. An account found with
// an expired login is not logged in, whatever its credential says. known is false when this process cannot tell.
func (a Accounts) LoginState(acct string) (loggedIn, known bool) {
	vendor := AccountVendor(a.Home, acct)
	if CheckVendor(vendor) != nil {
		return false, true
	}
	if a.LoginExpired(acct) != nil {
		return false, true
	}
	dir, _ := AccountConfigDir(a.Home, acct)
	return CredentialPresent(vendor, dir)
}

// LoggedIn is a confirmed login. Unknown is not one.
func (a Accounts) LoggedIn(acct string) bool { in, known := a.LoginState(acct); return in && known }

// MayStart is false only for a confirmed absence: a launch is refused for that, and for nothing it merely cannot see.
func (a Accounts) MayStart(acct string) bool {
	in, known := a.LoginState(acct)
	return !known || in
}

// GoneSince is from when the account is counted as gone from the graphs and forecasts; ok is false while it is not. A logged-out
// account is assumed not to come back: from the moment its expired login was found, or from now for one with no credential.
func (a Accounts) GoneSince(acct string, now float64) (float64, bool) {
	if m := a.LoginExpired(acct); m != nil {
		return m.At, true
	}
	if in, known := a.LoginState(acct); known && !in {
		return now, true
	}
	return 0, false
}

// LoginWord is a login state in words; known false is the sandboxed one.
func LoginWord(loggedIn, known, short bool) string {
	switch {
	case !known && short:
		return "?"
	case !known:
		return "unknown (sandboxed)"
	case short && loggedIn:
		return "yes"
	case short:
		return "no"
	case loggedIn:
		return "logged in"
	}
	return "NOT logged in"
}

// LoginCommand is the line a human runs to log in to the account.
func (a Accounts) LoginCommand(acct string) (string, error) {
	vendor := AccountVendor(a.Home, acct)
	if err := CheckVendor(vendor); err != nil {
		return "", err
	}
	dir, _ := AccountConfigDir(a.Home, acct)
	return fmt.Sprintf(`%s="%s" %s`, ConfigDirEnv(vendor), dir, strings.Join(LoginArgv(vendor), " ")), nil
}

// RecordedSubscriptionType is the plan already recorded for the account, read from disk alone.
func (a Accounts) RecordedSubscriptionType(acct string) (string, bool) {
	b, err := os.ReadFile(filepath.Join(paths.AccountDir(a.Home, acct), "subscription.json"))
	if err != nil {
		return "", false
	}
	var data map[string]any
	if json.Unmarshal(b, &data) != nil {
		return "", false
	}
	if v := data["subscriptionType"]; truthy(v) {
		return pyStr(v), true
	}
	return "", false
}

// UsersOf are the agents with a session open on the account. A closed agent is not one: it has no session to strand.
func (a Accounts) UsersOf(db *sql.DB, acct string) ([]string, error) {
	var out []string
	for _, name := range paths.ListAgents(a.Home) {
		var account sql.NullString
		var closed sql.NullInt64
		err := db.QueryRow("SELECT account, closed_at FROM agent_state WHERE agent = ?", name).Scan(&account, &closed)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if account.String == acct && closed.Int64 == 0 {
			out = append(out, name)
		}
	}
	return out, nil
}

// RemovePlan is whether the account may be removed, and the line to say about it. Refused while an agent has a session open on
// it: removing the account strands that session.
func (a Accounts) RemovePlan(db *sql.DB, acct string) (bool, string, error) {
	if !contains(paths.ListAccounts(a.Home), acct) {
		return false, fmt.Sprintf("no account '%s'", acct), nil
	}
	users, err := a.UsersOf(db, acct)
	if err != nil {
		return false, "", err
	}
	if len(users) > 0 {
		them := "them"
		if len(users) == 1 {
			them = "it"
		}
		return false, fmt.Sprintf("account '%s' has a session open for %s - close %s on the screen (c) first", acct, strings.Join(users, ", "), them), nil
	}
	return true, "", nil
}

// LogoutArgv is the vendor's own logout, run against this account's config directory: it removes its own credential, ocp
// never touches it. Empty when there is nothing logged in to remove.
func (a Accounts) LogoutArgv(acct string) ([]string, map[string]string) {
	if !a.LoggedIn(acct) {
		return nil, nil
	}
	if AccountVendor(a.Home, acct) == "opencode" {
		return []string{"opencode", "auth", "logout"}, a.EnvFor(acct, "")
	}
	return []string{"claude", "auth", "logout"}, a.EnvFor(acct, "")
}

// Retire takes the account off the roster by moving its directory aside, not deleting it: its transcripts are the only record
// of what was spent on it. A dot directory is invisible to ListAccounts.
func (a Accounts) Retire(acct string, now time.Time) (string, error) {
	dir := filepath.Join(paths.AccountsDir(a.Home), ".retired")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	dest := filepath.Join(dir, acct+"-"+now.Format("20060102-150405"))
	return dest, os.Rename(filepath.Join(paths.AccountsDir(a.Home), acct), dest)
}

// AccountNamedBy is the account a config-dir variable's value points into, or "" for a directory that is not one of ours.
func (a Accounts) AccountNamedBy(value string) string {
	root, ok := resolved(paths.AccountsDir(a.Home))
	if !ok {
		return ""
	}
	v, ok := resolved(value)
	if !ok || !strings.HasPrefix(v, root+"/") {
		return ""
	}
	first, _, _ := strings.Cut(strings.TrimPrefix(v, root+"/"), "/")
	return first
}

// EnvFor is the environment for launching this account's vendor as this account (and as agent, when one is named): a config-dir
// variable inherited from whoever launched this and pointing into an account goes, and the account's own is set. env is given
// as a map; both spellings of the handline variables are set.
func (a Accounts) EnvFor(acct, agent string) map[string]string {
	env := map[string]string{}
	for _, kv := range os.Environ() {
		k, v, _ := strings.Cut(kv, "=")
		env[k] = v
	}
	// Only one pointing into an account goes: XDG_DATA_HOME is also the user's own, and claude reads it.
	for _, vendor := range KnownVendors {
		k := ConfigDirEnv(vendor)
		if v := env[k]; v != "" && a.AccountNamedBy(v) != "" {
			delete(env, k)
		}
	}
	// An inherited OPENCODE_CONFIG_CONTENT is another session's whole config; OPENCODE_CONFIG_DIR stays.
	delete(env, "OPENCODE_CONFIG_CONTENT")
	vendor := AccountVendor(a.Home, acct)
	if dir, err := AccountConfigDir(a.Home, acct); err == nil {
		env[ConfigDirEnv(vendor)] = dir
	}
	paths.Setenv(env, "HOME", a.Home)
	paths.Setenv(env, "ACCOUNT", acct)
	if agent != "" {
		paths.Setenv(env, "AGENT", agent)
	}
	return paths.Both(env)
}
