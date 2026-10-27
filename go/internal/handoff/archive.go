package handoff

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
)

// Every record handed over with the agent's own note in it is kept, as it was handed, in agents/<a>/handoffs/<YYYYMMDD-HHMMSS>.md, so
// the notes have a history people and agents can read with plain tools. SweepArchive is the only thing that removes one.

const (
	// ArchiveLayout is the time in an archived record's file name.
	ArchiveLayout = "20060102-150405"
	// ArchiveKeep is how long an archived record is kept.
	ArchiveKeep = 90 * 24 * time.Hour
	// SlowedPrefix starts each line of a note that says what slowed the agent down (item 6 of AskTemplate).
	SlowedPrefix = "Slowed:"
)

// archive writes text as a new file of the agent's archive, named by the time now; a second record in the same second gets "-2".
func (e *Env) archive(agent, text string) error {
	dir := paths.HandoffArchiveDir(e.Home, agent)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	base := e.now().Format(ArchiveLayout)
	for n := 1; ; n++ {
		name := base
		if n > 1 {
			name = fmt.Sprintf("%s-%d", base, n)
		}
		f, err := os.OpenFile(filepath.Join(dir, name+".md"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if os.IsExist(err) {
			continue
		}
		if err != nil {
			return err
		}
		_, err = f.WriteString(text + "\n")
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		return err
	}
}

// archived is every archived record of every agent ("*") or of one, with the time in its name.
func archived(home, agent string) (map[string]time.Time, error) {
	files, err := filepath.Glob(filepath.Join(paths.HandoffArchiveDir(home, agent), "*.md"))
	if err != nil {
		return nil, err
	}
	out := map[string]time.Time{}
	for _, f := range files {
		name := filepath.Base(f)
		if len(name) < len(ArchiveLayout) {
			continue
		}
		if at, err := time.ParseInLocation(ArchiveLayout, name[:len(ArchiveLayout)], time.Local); err == nil {
			out[f] = at
		}
	}
	return out, nil
}

// SweepArchive removes the archived records older than ArchiveKeep (the housekeeping pass).
func SweepArchive(home string, now time.Time) error {
	files, err := archived(home, "*")
	if err != nil {
		return err
	}
	for f, at := range files {
		if now.Sub(at) > ArchiveKeep {
			if err := os.Remove(f); err != nil && !os.IsNotExist(err) {
				return err
			}
		}
	}
	return nil
}

// Slowed is one 'Slowed:' line of an archived record.
type Slowed struct {
	Agent string
	At    time.Time
	Line  string
}

// SlowedLines is every 'Slowed:' line of the records archived since since, of one agent or of all ("" or "*"), newest first.
// 'Slowed: none' is left out.
func SlowedLines(home, agent string, since time.Time) ([]Slowed, error) {
	if agent == "" {
		agent = "*"
	}
	files, err := archived(home, agent)
	if err != nil {
		return nil, err
	}
	var out []Slowed
	for f, at := range files {
		if at.Before(since) {
			continue
		}
		who := filepath.Base(filepath.Dir(filepath.Dir(f)))
		lines, err := slowedIn(f)
		if err != nil {
			return nil, err
		}
		for _, l := range lines {
			out = append(out, Slowed{Agent: who, At: at, Line: l})
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if !out[i].At.Equal(out[j].At) {
			return out[i].At.After(out[j].At)
		}
		return out[i].Agent < out[j].Agent
	})
	return out, nil
}

func slowedIn(file string) ([]string, error) {
	f, err := os.Open(file)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []string
	s := bufio.NewScanner(f)
	for s.Scan() {
		l := strings.TrimSpace(s.Text())
		rest, ok := strings.CutPrefix(l, SlowedPrefix)
		if !ok || strings.EqualFold(strings.TrimRight(strings.TrimSpace(rest), "."), "none") {
			continue
		}
		out = append(out, l)
	}
	return out, s.Err()
}
