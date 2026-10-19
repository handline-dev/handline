// Package worktreelock holds git's worktree lock on an agent's worktree while its session runs there, so nothing prunes or removes the
// worktree under it (`git worktree remove` and `prune` leave a locked one alone), and lets it go once that session's process is gone.
package worktreelock

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/peer"
)

var ours = regexp.MustCompile(`^handline: (\S+) runs here \(pid (\d+), started (\d+)\)$`)

// Running is whether process pid still runs and is the one that started at started (unix seconds): a pid given out again is another
// process, and its lock is a gone session's.
func Running(pid int, started int64) bool {
	t, ok := peer.Started(pid)
	return ok && t.Unix() == started
}

// holder is the session a lock reason of ours names: agent, pid, start; ok false for a lock that is not ours.
func holder(why string) (agent string, pid int, started int64, ok bool) {
	m := ours.FindStringSubmatch(why)
	if m == nil {
		return "", 0, 0, false
	}
	pid, _ = strconv.Atoi(m[2])
	started, _ = strconv.ParseInt(m[3], 10, 64)
	return m[1], pid, started, true
}

// canonical is p with its links resolved (macOS's /var is /private/var) as far as it exists: a worktree whose folder is gone still
// compares equal to the path git lists for it.
func canonical(p string) string {
	p = filepath.Clean(p)
	for cur, rest := p, ""; ; cur, rest = filepath.Dir(cur), filepath.Join(filepath.Base(cur), rest) {
		if real, err := filepath.EvalSymlinks(cur); err == nil {
			return filepath.Join(real, rest)
		}
		if cur == filepath.Dir(cur) {
			return p
		}
	}
}

func reason(agent string, pid int, started int64) string {
	return fmt.Sprintf("handline: %s runs here (pid %d, started %d)", agent, pid, started)
}

// lockOf is the worktree dir is in: its top, whether it is a linked worktree (the main one cannot be locked, and a folder that is no
// checkout is none), and the lock reason it carries ("" with locked false for none).
func lockOf(dir string) (top string, linked, locked bool, why string, err error) {
	r := gitrun.Run(dir, "rev-parse", "--show-toplevel")
	if r.RC != 0 {
		return "", false, false, "", nil
	}
	top = filepath.Clean(strings.TrimSpace(r.Stdout))
	linked, locked, why, err = entry(dir, top)
	return top, linked, locked, why, err
}

// entry is path's lock as repo lists its worktrees: read from the repository, so a worktree whose folder is gone still has one.
func entry(repo, path string) (linked, locked bool, why string, err error) {
	r := gitrun.Run(repo, "worktree", "list", "--porcelain")
	if r.RC != 0 {
		return false, false, "", fmt.Errorf("git worktree list: %s", strings.TrimSpace(r.Stderr))
	}
	for i, e := range strings.Split(strings.TrimSpace(r.Stdout), "\n\n") {
		lines := strings.Split(e, "\n")
		p, _ := strings.CutPrefix(lines[0], "worktree ")
		if canonical(p) != canonical(path) {
			continue
		}
		for _, l := range lines[1:] {
			if l == "locked" || strings.HasPrefix(l, "locked ") {
				locked, why = true, strings.TrimPrefix(strings.TrimPrefix(l, "locked"), " ")
			}
		}
		return i > 0, locked, why, nil
	}
	return false, false, "", nil
}

// Hold locks the worktree dir is in for agent's session, process pid started at started. A lock another session still holds is left
// (and said); one a session that is gone left is taken over. The main worktree cannot be locked and is left as it is.
func Hold(dir, agent string, pid int, started int64, running func(int, int64) bool) error {
	top, linked, locked, why, err := lockOf(dir)
	if err != nil || !linked {
		return err
	}
	if locked {
		who, n, at, ok := holder(why)
		if !ok {
			return nil // a person's own lock: theirs to lift
		}
		if n != pid && running(n, at) {
			return fmt.Errorf("%s is locked for %s, which still runs (pid %d)", top, who, n)
		}
		if r := gitrun.Run(top, "worktree", "unlock", top); r.RC != 0 {
			return fmt.Errorf("git worktree unlock: %s", strings.TrimSpace(r.Stderr))
		}
	}
	if r := gitrun.Run(top, "worktree", "lock", "--reason", reason(agent, pid, started), top); r.RC != 0 {
		return fmt.Errorf("git worktree lock: %s", strings.TrimSpace(r.Stderr))
	}
	return nil
}

// Release unlocks repo's worktree at path, its folder there or gone, when its lock is a session's that is gone; any other lock is left.
// It says whether it unlocked.
func Release(repo, path string, running func(int, int64) bool) (bool, error) {
	linked, locked, why, err := entry(repo, path)
	if err != nil || !linked || !locked {
		return false, err
	}
	if _, n, at, ok := holder(why); !ok || running(n, at) {
		return false, nil
	}
	if r := gitrun.Run(repo, "worktree", "unlock", path); r.RC != 0 {
		return false, fmt.Errorf("git worktree unlock: %s", strings.TrimSpace(r.Stderr))
	}
	return true, nil
}

// Held is the agent whose live session holds repo's worktree at path, "" for none.
func Held(repo, path string, running func(int, int64) bool) string {
	if _, locked, why, err := entry(repo, path); err == nil && locked {
		if agent, n, at, ok := holder(why); ok && running(n, at) {
			return agent
		}
	}
	return ""
}

// Linked is every linked worktree of the repository dir is in: where a retired agent's lock may still stand.
func Linked(dir string) []string {
	r := gitrun.Run(dir, "worktree", "list", "--porcelain")
	if r.RC != 0 {
		return nil
	}
	var out []string
	for i, entry := range strings.Split(strings.TrimSpace(r.Stdout), "\n\n") {
		if path, ok := strings.CutPrefix(strings.SplitN(entry, "\n", 2)[0], "worktree "); ok && i > 0 {
			out = append(out, path)
		}
	}
	return out
}
