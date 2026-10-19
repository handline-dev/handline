// Package worktreelock holds git's worktree lock on an agent's worktree while its session runs there, so nothing prunes or removes the
// worktree under it (`git worktree remove` and `prune` leave a locked one alone), and lets it go once that session's process is gone.
package worktreelock

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/gitrun"
)

var ours = regexp.MustCompile(`^handline: (\S+) runs here \(pid (\d+)\)$`)

// Alive is whether process pid still runs.
func Alive(pid int) bool { return pid > 0 && syscall.Kill(pid, 0) == nil }

// canonical is p with its links resolved (macOS's /var is /private/var), or cleaned when it cannot be.
func canonical(p string) string {
	if real, err := filepath.EvalSymlinks(p); err == nil {
		return real
	}
	return filepath.Clean(p)
}

func reason(agent string, pid int) string {
	return fmt.Sprintf("handline: %s runs here (pid %d)", agent, pid)
}

// lockOf is the worktree dir is in: its top, whether it is a linked worktree (the main one cannot be locked, and a folder that is no
// checkout is none), and the lock reason it carries ("" with locked false for none).
func lockOf(dir string) (top string, linked, locked bool, why string, err error) {
	r := gitrun.Run(dir, "rev-parse", "--show-toplevel")
	if r.RC != 0 {
		return "", false, false, "", nil
	}
	top = filepath.Clean(strings.TrimSpace(r.Stdout))
	r = gitrun.Run(dir, "worktree", "list", "--porcelain")
	if r.RC != 0 {
		return top, false, false, "", fmt.Errorf("git worktree list: %s", strings.TrimSpace(r.Stderr))
	}
	for i, entry := range strings.Split(strings.TrimSpace(r.Stdout), "\n\n") {
		lines := strings.Split(entry, "\n")
		path, _ := strings.CutPrefix(lines[0], "worktree ")
		if canonical(path) != canonical(top) {
			continue
		}
		for _, l := range lines[1:] {
			if l == "locked" || strings.HasPrefix(l, "locked ") {
				locked, why = true, strings.TrimPrefix(strings.TrimPrefix(l, "locked"), " ")
			}
		}
		return top, i > 0, locked, why, nil
	}
	return top, false, false, "", nil
}

// Hold locks the worktree dir is in for agent's session, process pid. A lock another session still holds is left (and said); one a
// session that is gone left is taken over. The main worktree cannot be locked and is left as it is.
func Hold(dir, agent string, pid int, alive func(int) bool) error {
	top, linked, locked, why, err := lockOf(dir)
	if err != nil || !linked {
		return err
	}
	if locked {
		m := ours.FindStringSubmatch(why)
		if m == nil {
			return nil // a person's own lock: theirs to lift
		}
		if n, _ := strconv.Atoi(m[2]); n != pid && alive(n) {
			return fmt.Errorf("%s is locked for %s, which still runs (pid %d)", top, m[1], n)
		}
		if r := gitrun.Run(top, "worktree", "unlock", top); r.RC != 0 {
			return fmt.Errorf("git worktree unlock: %s", strings.TrimSpace(r.Stderr))
		}
	}
	if r := gitrun.Run(top, "worktree", "lock", "--reason", reason(agent, pid), top); r.RC != 0 {
		return fmt.Errorf("git worktree lock: %s", strings.TrimSpace(r.Stderr))
	}
	return nil
}

// Release unlocks the worktree dir is in when its lock is a session's that is gone; any other lock is left. It says whether it unlocked.
func Release(dir string, alive func(int) bool) (bool, error) {
	top, linked, locked, why, err := lockOf(dir)
	if err != nil || !linked || !locked {
		return false, err
	}
	m := ours.FindStringSubmatch(why)
	if m == nil {
		return false, nil
	}
	if n, _ := strconv.Atoi(m[2]); alive(n) {
		return false, nil
	}
	if r := gitrun.Run(top, "worktree", "unlock", top); r.RC != 0 {
		return false, fmt.Errorf("git worktree unlock: %s", strings.TrimSpace(r.Stderr))
	}
	return true, nil
}
