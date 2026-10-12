// Package backup reads the snapshots `hl backup` keeps beside the home (handline/infra/backup.py): the gate a command that cannot be undone
// asks before it goes ahead. It makes none.
package backup

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/timefmt"
)

const (
	prefix      = "orcapipe-"
	suffix      = ".tar.gz"
	fingerprint = ".last-fingerprint"
	// GateMaxAgeS is how young a backup must be: five minutes, since the job runs every minute and a person making one has just done it.
	GateMaxAgeS = 300
)

// Dir is where the snapshots of home are kept: beside it, named for it.
func Dir(home string) string {
	home = filepath.Clean(home)
	return filepath.Join(filepath.Dir(home), filepath.Base(home)+"-backups")
}

// Newest is the path and time of the newest snapshot; "" when there is none.
func Newest(home string) (path string, taken time.Time) {
	m, _ := filepath.Glob(filepath.Join(Dir(home), prefix+"*"+suffix))
	for _, p := range m {
		st, err := os.Stat(p)
		if err != nil {
			continue
		}
		if path == "" || st.ModTime().After(taken) {
			path, taken = p, st.ModTime()
		}
	}
	return path, taken
}

// CurrentAsOf is when the newest snapshot last matched the state: when it was taken, or later if a check since found nothing changed.
func CurrentAsOf(home string) (time.Time, bool) {
	path, taken := Newest(home)
	if path == "" {
		return time.Time{}, false
	}
	if st, err := os.Stat(filepath.Join(Dir(home), fingerprint)); err == nil && st.ModTime().After(taken) {
		return st.ModTime(), true
	}
	return taken, true
}

func ago(now, then time.Time) string { return timefmt.Ago(int64(now.Sub(then).Seconds())) }

// Describe is the newest backup's path and time, in one line.
func Describe(home string, now time.Time) string {
	path, taken := Newest(home)
	if path == "" {
		return "newest backup: none in " + Dir(home)
	}
	line := fmt.Sprintf("newest backup: %s (%s, %s ago)", path, timefmt.Stamp(taken.Unix(), "2006-01-02 15:04"), ago(now, taken))
	if as, _ := CurrentAsOf(home); as.Sub(taken) > time.Second {
		line += "; checked unchanged " + ago(now, as) + " ago"
	}
	return line
}

// RequireRecent is "" when a backup of home is at most GateMaxAgeS old, otherwise why not and the command that makes one. A backup counts
// as that old when a snapshot was taken then, or checked unchanged then.
func RequireRecent(home string, now time.Time) string {
	if as, ok := CurrentAsOf(home); ok && now.Sub(as) <= GateMaxAgeS*time.Second {
		return ""
	}
	seen := "there is no backup yet"
	if path, taken := Newest(home); path != "" {
		seen = fmt.Sprintf("the newest, %s, was taken %s ago", strings.TrimPrefix(path, Dir(home)+"/"), ago(now, taken))
	}
	return fmt.Sprintf("no backup from the last %d minutes (%s). Make one with `hl backup`, then run this again.", GateMaxAgeS/60, seen)
}
