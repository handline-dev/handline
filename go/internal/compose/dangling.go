package compose

import (
	"context"
	"errors"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/dangling"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/team"
)

// danglingPorts is dangling.Ports over the daemon's terminal host, the session files and the process table. deliver is the peer inbox's,
// unless a test hands in its own.
type danglingPorts struct {
	p       *Parts
	deliver func(ctx context.Context, sess peerinbox.Session, text string, from peerinbox.From) peerinbox.Result
}

var _ dangling.Ports = danglingPorts{}

func (d danglingPorts) Live() map[string]bool { return d.p.LC.term.LiveTerminals() }

func (d danglingPorts) PaneStarted(handle string) int64 {
	pid := d.p.LC.term.PanePid(handle)
	if pid <= 0 {
		return 0
	}
	return termRun{}.PidStart(pid)
}

// PaneSession is the live session, in any account's session files, whose process runs inside the pane.
func (d danglingPorts) PaneSession(handle string) (int, bool) {
	panePid := d.p.LC.term.PanePid(handle)
	if panePid <= 0 {
		return 0, false
	}
	parent := parents()
	if parent == nil {
		return 0, false
	}
	for _, dir := range d.configDirs() {
		files, _ := filepath.Glob(filepath.Join(dir, "sessions", "*.json"))
		for _, f := range files {
			pid, err := strconv.Atoi(strings.TrimSuffix(filepath.Base(f), ".json"))
			if err == nil && pid > 0 && alive(pid) && under(parent, pid, panePid) {
				return pid, true
			}
		}
	}
	return 0, true
}

func (d danglingPorts) configDirs() []string {
	var out []string
	for _, acct := range paths.ListAccounts(d.p.home) {
		if dir, err := team.AccountConfigDir(d.p.home, acct); err == nil {
			out = append(out, dir)
		}
	}
	return out
}

func (d danglingPorts) Ask(pid int, text string) error {
	for _, dir := range d.configDirs() {
		sess, err := peerinbox.ByPID(dir, pid)
		if err != nil {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		deliver := d.deliver
		if deliver == nil {
			deliver = peerinbox.New().Deliver
		}
		if res := deliver(ctx, sess, text, peerinbox.From{Name: dangling.Requester}); res.Kind != peerinbox.Delivered {
			return errors.New(res.Why)
		}
		return nil
	}
	return peerinbox.ErrNoSession
}

func (d danglingPorts) Close(handle string) (bool, string) { return d.p.LC.term.CloseTerminal(handle) }
func (danglingPorts) PidGone(pid int) bool                 { return !alive(pid) }
func (danglingPorts) PidStart(pid int) int64               { return termRun{}.PidStart(pid) }
func (danglingPorts) Kill(pid int) error                   { return syscall.Kill(pid, syscall.SIGTERM) }

func (d danglingPorts) Tell(to, text string) error {
	return d.p.LC.Env.SystemSay(dangling.Requester, to, text)
}
