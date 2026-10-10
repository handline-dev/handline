package housekeeping

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	// StaleAfter is how long a repo copy in the scratch root must stand untouched before it is deleted: its check is long over by then.
	StaleAfter = 24 * time.Hour
	// ScratchEvery is how often the root is swept: it can hold tens of thousands of entries.
	ScratchEvery = time.Hour
)

// ScratchRoot is the Claude sessions' shared scratch root, /private/tmp/claude-<uid>.
func ScratchRoot() string { return fmt.Sprintf("/private/tmp/claude-%d", os.Getuid()) }

// Scratch deletes the stale repo copies in the sessions' scratch root. Agents copy the repo there, node_modules and all (~1.4 GB each), to
// run a check, and leave it behind: 185 of them filled 250 GB in three days. A copy is a directory directly under the root holding
// AGENTS.md and node_modules; nothing else there is touched.
type Scratch struct {
	Root string
	Log  func(string)

	mu      sync.Mutex
	at      time.Time
	running bool
}

func isRepoCopy(d string) bool {
	if st, err := os.Stat(filepath.Join(d, "AGENTS.md")); err != nil || !st.Mode().IsRegular() {
		return false
	}
	st, err := os.Stat(filepath.Join(d, "node_modules"))
	return err == nil && st.IsDir()
}

// lastTouched is the newest mtime of the copy and its top-level entries: a check writing anywhere at the top keeps the copy alive.
func lastTouched(d string) (time.Time, error) {
	st, err := os.Lstat(d)
	if err != nil {
		return time.Time{}, err
	}
	newest := st.ModTime()
	entries, _ := os.ReadDir(d)
	for _, e := range entries {
		if info, err := e.Info(); err == nil && info.ModTime().After(newest) {
			newest = info.ModTime()
		}
	}
	return newest, nil
}

// Sweep deletes the copies under root untouched for StaleAfter and returns their names.
func Sweep(root string, now time.Time) []string {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	var removed []string
	for _, e := range entries {
		d := filepath.Join(root, e.Name())
		if !e.IsDir() || !isRepoCopy(d) {
			continue
		}
		if t, err := lastTouched(d); err != nil || now.Sub(t) < StaleAfter {
			continue
		}
		// node_modules first: a restart mid-delete then leaves either a copy the next sweep still knows or a few MB of source, never
		// 1.4 GB it no longer recognises.
		os.RemoveAll(filepath.Join(d, "node_modules"))
		os.RemoveAll(d)
		if _, err := os.Lstat(d); err != nil {
			removed = append(removed, e.Name())
		}
	}
	return removed
}

// Maybe sweeps at most once per ScratchEvery on a goroutine of its own, since one copy is ~48k files and the tick must not wait for that;
// none starts while the last is still deleting. It returns a channel that closes when the sweep it started is done, nil if none started.
func (s *Scratch) Maybe(now time.Time) <-chan struct{} {
	s.mu.Lock()
	if s.Root == "" || s.running || now.Sub(s.at) < ScratchEvery {
		s.mu.Unlock()
		return nil
	}
	s.at, s.running = now, true
	s.mu.Unlock()
	done := make(chan struct{})
	go func() {
		defer close(done)
		removed := Sweep(s.Root, now)
		s.mu.Lock()
		s.running = false
		s.mu.Unlock()
		if len(removed) > 0 {
			s.Log(fmt.Sprintf("scratch: deleted %d repo cop%s untouched for a day: %s", len(removed), map[bool]string{true: "y", false: "ies"}[len(removed) == 1], strings.Join(removed, ", ")))
		}
	}()
	return done
}
