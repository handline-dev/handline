package daemon

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/tick"
)

var shaShape = regexp.MustCompile(`^[0-9a-f]{7,40}$`)

// versionOf is what `exe --version` prints, within the start check's limit; a test replaces it.
var versionOf = func(exe string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), tick.CheckTimeout)
	defer cancel()
	out, err := exec.CommandContext(ctx, exe, "--version").Output()
	return strings.TrimSpace(string(out)), err
}

// execve replaces this process; a test replaces it.
var execve = syscall.Exec

// upgrade is daemon.upgrade: the app hands over the handlined it built and that build's sha. The binary must start and say it is that
// sha; then the reply goes out, and the daemon stops as a signal stops it (the tick in hand finishes, the lock and the database are
// given up) and main calls ExecUpgrade.
func (d *Daemon) upgrade(c *rpc.Conn, p map[string]any) (any, error) {
	exe, _ := p["exe"].(string)
	if !filepath.IsAbs(exe) {
		return nil, rpc.BadRequestError("daemon.upgrade needs exe, an absolute path to a handlined", "exe", rpc.InvalidParams)
	}
	sha, _ := p["sha"].(string)
	if !shaShape.MatchString(sha) {
		return nil, rpc.BadRequestError("daemon.upgrade needs sha, the git sha the binary was built from", "sha", rpc.InvalidParams)
	}
	got, err := versionOf(exe)
	if err != nil {
		return nil, rpc.Refused(fmt.Sprintf("%s does not start (%v), so the daemon stays on %s", exe, err, c.Server().Sha), "build", "")
	}
	// A release prints <version>+<sha>[-dirty], a checkout build the sha alone.
	if got != sha && !strings.Contains(got, "+"+sha) {
		return nil, rpc.Refused(fmt.Sprintf("%s says it is build %q, not %s, so the daemon stays on %s", exe, got, sha, c.Server().Sha),
			"build", "")
	}
	d.cfg.Log("  daemon.upgrade: restarting onto %s (%s) after this reply", exe, sha)
	c.AfterReply(func() {
		d.mu.Lock()
		defer d.mu.Unlock()
		if d.upgradeTo == "" {
			d.upgradeTo = exe
			close(d.upgrading)
		}
	})
	return map[string]any{"from": c.Server().Sha, "to": sha, "exe": exe}, nil
}

// Upgrading closes once a daemon.upgrade has been answered: the daemon should stop and call ExecUpgrade.
func (d *Daemon) Upgrading() <-chan struct{} { return d.upgrading }

// ExecUpgrade execs the binary daemon.upgrade was given, with this process's arguments, once Close has given up the lock and the
// database. It does nothing when no upgrade was asked, and returns only when the exec fails.
func (d *Daemon) ExecUpgrade() error {
	d.mu.Lock()
	exe := d.upgradeTo
	d.mu.Unlock()
	if exe == "" {
		return nil
	}
	return execve(exe, append([]string{exe}, os.Args[1:]...), os.Environ())
}
