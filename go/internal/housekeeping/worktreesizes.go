package housekeeping

import (
	"fmt"
	"strconv"
	"strings"
	"sync"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/worktreeprune"
)

// WorktreeSizes measures every worktree, an agent's or an orphan (worktreeprune.Orphans), at most every worktreeprune.SizeEvery seconds,
// off the tick since a walk takes a while, and keeps each one's bytes in meta (worktreeprune.SizePrefix) for worktrees.list and the
// disk item. It never deletes a worktree.
type WorktreeSizes struct {
	Home string
	Log  func(string)
	// Size is the bytes under a path; nil is worktreeprune.Size.
	Size func(path string) (int64, error)
	// Worktrees are the worktrees to measure; nil is WorktreePaths of Home.
	Worktrees func(q db.Q) ([]string, error)

	measured int64
	mu       sync.Mutex
	running  bool
	done     map[string]int64 // a finished walk's sizes, written by the next Check
}

// Check writes what the last walk measured, then starts the next walk when one is due.
func (w *WorktreeSizes) Check(q db.Q, now int64) error {
	w.mu.Lock()
	done, running := w.done, w.running
	w.done = nil
	w.mu.Unlock()
	if done != nil {
		if err := keepSizes(q, done); err != nil {
			return err
		}
	}
	if running || (w.measured != 0 && now-w.measured < worktreeprune.SizeEvery) {
		return nil
	}
	list := w.Worktrees
	if list == nil {
		list = func(q db.Q) ([]string, error) { return WorktreePaths(q, w.Home) }
	}
	paths, err := list(q)
	if err != nil {
		return err
	}
	w.measured = now
	w.mu.Lock()
	w.running = true
	w.mu.Unlock()
	size := w.Size
	if size == nil {
		size = worktreeprune.Size
	}
	go func() {
		sizes := map[string]int64{}
		for _, p := range paths {
			if n, err := size(p); err == nil {
				sizes[p] = n
			} else {
				w.Log(fmt.Sprintf("worktree sizes: %s: %v", p, err))
			}
		}
		w.mu.Lock()
		w.done, w.running = sizes, false
		w.mu.Unlock()
	}()
	return nil
}

// WorktreePaths are the real paths of every worktree an agent records and of the orphans of every repository an agent works in.
func WorktreePaths(q db.Q, home string) ([]string, error) {
	agents, err := worktreeprune.AgentWorktrees(q, "")
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(agents))
	for p := range agents {
		out = append(out, p)
	}
	for _, repo := range worktreeprune.Repos(home) {
		for _, wt := range worktreeprune.Orphans(repo, agents) {
			out = append(out, worktreeprune.Real(wt.Path))
		}
	}
	return out, nil
}

// keepSizes replaces the kept sizes with sizes: a worktree gone or no longer listed, loses its row.
func keepSizes(q db.Q, sizes map[string]int64) error {
	names, err := db.MetaNames(q, worktreeprune.SizePrefix)
	if err != nil {
		return err
	}
	for _, name := range names {
		if _, ok := sizes[strings.TrimPrefix(name, worktreeprune.SizePrefix)]; !ok {
			if err := db.ClearMeta(q, name); err != nil {
				return err
			}
		}
	}
	for p, n := range sizes {
		if err := db.SetMeta(q, worktreeprune.SizePrefix+p, strconv.FormatInt(n, 10)); err != nil {
			return err
		}
	}
	return nil
}

// OrphanSizes are the orphan worktrees as the last walk measured them: how many, and the bytes they take together.
func OrphanSizes(q db.Q) (count, bytes int64, err error) {
	err = q.QueryRow("SELECT count(*), COALESCE(sum(CAST(value AS INTEGER)), 0) FROM meta WHERE substr(name, 1, ?) = ?",
		len(worktreeprune.SizePrefix), worktreeprune.SizePrefix).Scan(&count, &bytes)
	return count, bytes, err
}

// OrphanText is the orphans in plain words, for after the disk item's text.
func OrphanText(count, bytes int64) string {
	noun := "worktrees"
	if count == 1 {
		noun = "worktree"
	}
	return fmt.Sprintf("%d %s that no agent works in take %s; the worktree list can remove them.", count, noun, sizeText(bytes))
}
