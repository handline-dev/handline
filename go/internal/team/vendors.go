package team

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// The vendors a driver exists for (handline/vendors/__init__.py). Only what the account and agent files need is here: the
// name, the folder an account keeps its config in, how a login is told, how it is started.
const (
	DefaultVendor = "claude"
	opencode      = "opencode"
)

// KnownVendors is the vendors with a driver, sorted.
var KnownVendors = []string{DefaultVendor, opencode}

// UnknownVendor is the refusal for a vendor with no driver.
type UnknownVendor struct{ Vendor string }

func (e *UnknownVendor) Error() string {
	return fmt.Sprintf("no driver for vendor %q; known: %s", e.Vendor, strings.Join(KnownVendors, ", "))
}

// CheckVendor is nil for a vendor with a driver.
func CheckVendor(vendor string) error {
	for _, v := range KnownVendors {
		if v == vendor {
			return nil
		}
	}
	return &UnknownVendor{vendor}
}

// ConfigDirName is the folder under an account that the vendor's config-dir variable names.
func ConfigDirName(vendor string) string {
	if vendor == opencode {
		return "xdg-data"
	}
	return ".claude"
}

// ConfigDirEnv is the variable that moves the credential.
func ConfigDirEnv(vendor string) string {
	if vendor == opencode {
		return "XDG_DATA_HOME"
	}
	return "CLAUDE_CONFIG_DIR"
}

// LoginArgv is the command a human runs to log in.
func LoginArgv(vendor string) []string {
	if vendor == opencode {
		return []string{"opencode", "auth", "login"}
	}
	return []string{"claude", "/login"}
}

// AccountVendor is the vendor an account is an account of. An account that records none predates vendors and is the default's;
// a file that is there but empty is not a vendor, so it names none and every driver lookup refuses it.
func AccountVendor(home, acct string) string {
	b, err := os.ReadFile(filepath.Join(paths.AccountDir(home, acct), "vendor"))
	if err != nil {
		return DefaultVendor
	}
	return strings.TrimSpace(string(b))
}

// AccountConfigDir is the directory the account's config-dir variable names; an error when its vendor has no driver.
func AccountConfigDir(home, acct string) (string, error) {
	v := AccountVendor(home, acct)
	if err := CheckVendor(v); err != nil {
		return "", err
	}
	return filepath.Join(paths.AccountDir(home, acct), ConfigDirName(v)), nil
}

// KeychainService is the macOS Keychain service name Claude Code uses for a config dir: the default ~/.claude uses
// "Claude Code-credentials", any other dir that name plus the first 8 hex of the sha256 of its path.
func KeychainService(configDir string) string {
	if configDir == filepath.Join(paths.UserHome(), ".claude") {
		return "Claude Code-credentials"
	}
	h := sha256.Sum256([]byte(configDir))
	return "Claude Code-credentials-" + hex.EncodeToString(h[:])[:8]
}

// run is a subprocess's exit code (-1 when it could not run) and output, with a limit.
func run(limit time.Duration, env []string, argv ...string) (int, string) {
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	if env != nil {
		cmd.Env = env
	}
	out, err := cmd.Output()
	if err == nil {
		return 0, string(out)
	}
	if ee, ok := err.(*exec.ExitError); ok && ctx.Err() == nil {
		return ee.ExitCode(), string(out)
	}
	return -1, string(out)
}

// CredentialPresent is whether the account's driver finds a credential: a file's existence for claude (or, on macOS, the
// keychain item's presence, asked without -w so the secret is never read), a run of `opencode auth list` for opencode. The
// third value is false when this process cannot tell (a sandboxed one cannot see the keychain).
func CredentialPresent(vendor, configDir string) (present, known bool) {
	if vendor == opencode {
		if _, err := os.Stat(filepath.Join(configDir, "opencode", "auth.json")); err != nil {
			return false, true
		}
		exe, err := exec.LookPath("opencode")
		if err != nil {
			return false, true
		}
		rc, out := run(20*time.Second, append(os.Environ(), ConfigDirEnv(vendor)+"="+configDir), exe, "auth", "list")
		_ = rc
		return countsCredentials(out), true
	}
	if _, err := os.Stat(filepath.Join(configDir, ".credentials.json")); err == nil {
		return true, true // file-backed storage (Linux, and macOS installs that opt out of Keychain)
	}
	if runtime.GOOS != "darwin" {
		return false, true
	}
	rc, _ := run(10*time.Second, nil, "security", "find-generic-password", "-s", KeychainService(configDir))
	if rc == -1 {
		return false, true
	}
	if rc != 0 && os.Getenv("SANDBOX_RUNTIME") != "" {
		return false, false
	}
	return rc == 0, true
}

var credentialsRe = regexp.MustCompile(`\b(\d+) credentials\b`)

// countsCredentials is whether `opencode auth list` printed a count of credentials above zero.
func countsCredentials(out string) bool {
	m := credentialsRe.FindStringSubmatch(out)
	if m == nil {
		return false
	}
	n, err := strconv.Atoi(m[1])
	return err == nil && n > 0
}

func isDarwin() bool { return runtime.GOOS == "darwin" }

// yamlFloat is a float as Python's json.dumps writes it.
func yamlFloat(f float64) string { return yamlcompat.FloatRepr(f) }
