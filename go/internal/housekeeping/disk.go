package housekeeping

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/sysres"
)

const (
	GB = int64(1) << 30
	// DiskLow is the free space under which the founder gets one My turn card; it is not raised again until free space is back at DiskLow
	// plus DiskMargin, so a volume that hovers at the line does not raise card after card.
	DiskLow       = 10 * GB
	DiskMargin    = 2 * GB
	DiskEvery     = int64(600)
	diskToldLevel = "disk_low_told"
	// testDirAge is how old a test's temp dir must be to be named as one to clear.
	testDirAge = 24 * 60 * 60
)

// DiskWatch raises a My turn card when the volume the home is on runs low on free space, naming what can be cleared. It reads and tells;
// it never deletes.
type DiskWatch struct {
	Home string
	// Raise puts the card in front of the founder.
	Raise func(q db.Q, text string) error
	Log   func(string)
	// Free is the free and total bytes of the volume holding a path; nil is statfs.
	Free func(path string) (free, total int64, err error)

	checked int64
}

// Check runs at most every DiskEvery seconds. That the card is up is kept in the database, so a restart does not raise it again.
func (w *DiskWatch) Check(q db.Q, now int64) error {
	if w.checked != 0 && now-w.checked < DiskEvery {
		return nil
	}
	w.checked = now
	free, total, err := w.free()(w.Home)
	if err != nil {
		return err
	}
	told, err := db.Cursor(q, diskToldLevel)
	if err != nil {
		return err
	}
	if told != 0 && free >= DiskLow+DiskMargin {
		w.Log(fmt.Sprintf("disk: %d GB free again", free/GB))
		return db.ClearCursor(q, diskToldLevel)
	}
	if told != 0 || free >= DiskLow {
		return nil
	}
	text := fmt.Sprintf("disk %d GB left (of %d GB on the volume %s is on). Nothing was deleted; what can be cleared:\n%s",
		free/GB, total/GB, w.Home, strings.Join(w.candidates(q, now), "\n"))
	if err := w.Raise(q, text); err != nil {
		return err
	}
	w.Log(fmt.Sprintf("disk: %d GB free - card raised", free/GB))
	return db.SetCursor(q, diskToldLevel, 1)
}

func (w *DiskWatch) free() func(string) (int64, int64, error) {
	if w.Free != nil {
		return w.Free
	}
	return sysres.StatfsFree
}

// candidates are the usual growers, named and not measured, since walking them takes minutes.
func (w *DiskWatch) candidates(q db.Q, now int64) []string {
	out := []string{"- the Go build cache: " + goBuildCache() + " (go clean -cache)", "- the pnpm store: " + pnpmStore() + " (pnpm store prune)",
		"- the sessions' scratch: " + ScratchRoot()}
	if old := oldTestDirs(os.TempDir(), now); len(old) > 0 {
		out = append(out, fmt.Sprintf("- %d test temp dirs older than a day in %s", len(old), os.TempDir()))
	}
	for _, wt := range extraWorktrees(q) {
		out = append(out, "- a worktree an agent was given for a past task: "+wt)
	}
	return out
}

// pnpmStore is where pnpm keeps its store when PNPM_HOME does not say: its default on macOS.
func pnpmStore() string {
	if h := os.Getenv("PNPM_HOME"); h != "" {
		return filepath.Join(h, "store")
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, "Library", "pnpm", "store")
}

// oldTestDirs are go test's and the suites' temp dirs (Test*, handline-*) in dir untouched for testDirAge.
func oldTestDirs(dir string, now int64) []string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		name := e.Name()
		if !e.IsDir() || !(strings.HasPrefix(name, "Test") || strings.HasPrefix(name, "handline-")) {
			continue
		}
		if info, err := e.Info(); err == nil && now-info.ModTime().Unix() >= testDirAge {
			out = append(out, filepath.Join(dir, name))
		}
	}
	return out
}

// extraWorktrees are the worktrees agents still record beyond their own (agent_state.extra_worktrees) that exist on disk.
func extraWorktrees(q db.Q) []string {
	rows, err := q.Query("SELECT extra_worktrees FROM agent_state WHERE COALESCE(extra_worktrees, '') NOT IN ('', '[]')")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var text string
		var list []string
		if rows.Scan(&text) != nil || json.Unmarshal([]byte(text), &list) != nil {
			continue
		}
		for _, p := range list {
			if _, err := os.Stat(p); err == nil {
				out = append(out, p)
			}
		}
	}
	return out
}

// goBuildCache is where go keeps its build cache when GOCACHE does not say: named, not measured, since walking it takes minutes.
func goBuildCache() string {
	if c := os.Getenv("GOCACHE"); c != "" {
		return c
	}
	dir, err := os.UserCacheDir()
	if err != nil {
		return "go env GOCACHE"
	}
	return filepath.Join(dir, "go-build")
}
