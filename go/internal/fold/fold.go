// Package fold is the digest upkeep that is not a decision: the midnight rollover, which carries yesterday's open items into
// today's digest with no model, and agent.fold, which rewrites a digest from the event delta with the one model call the system has.
package fold

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

// Timeout is how long the fold's model call may run (fold.FOLD_TIMEOUT_S).
const Timeout = 180 * time.Second

// Env is what the two need; Fold also needs DB, Usage and Accounts.
type Env struct {
	Home     string
	DB       *sql.DB
	Usage    *usage.Env
	Accounts team.Accounts
	Timeout  time.Duration // zero is Timeout
	Today    func() string
}

func (e *Env) today() string {
	if e.Today != nil {
		return e.Today()
	}
	return time.Now().Format("2006-01-02")
}

// Rollover is fold.rollover: the first tick of a new day writes today's digest from the latest earlier one, so yesterday's "in progress"
// does not stay in progress and a fold after midnight does not have to repeat it.
func (e *Env) Rollover(agent string) (bool, string) {
	day := e.today()
	dir := filepath.Join(paths.AgentDir(e.Home, agent), "journal")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return false, fmt.Sprintf("%s: rollover failed - %v", agent, err)
	}
	target := filepath.Join(dir, day+".md")
	if _, err := os.Stat(target); err == nil {
		return true, fmt.Sprintf("%s: %s digest already exists", agent, day)
	}
	names, _ := filepath.Glob(filepath.Join(dir, "*.md"))
	var earlier []string
	for _, n := range names {
		if filepath.Base(n) != day+".md" {
			earlier = append(earlier, n)
		}
	}
	if len(earlier) == 0 {
		return true, agent + ": nothing to roll over"
	}
	sort.Strings(earlier)
	last := earlier[len(earlier)-1]
	raw, err := os.ReadFile(last)
	if err != nil {
		return false, fmt.Sprintf("%s: rollover failed - %v", agent, err)
	}
	previous := handoff.ParseDigest(string(raw))
	carried := handoff.CarryForward(previous, day)
	carried.Facts = previous.Facts
	if err := atomicfile.Write(target, carried.Render()); err != nil {
		return false, fmt.Sprintf("%s: rollover failed - %v", agent, err)
	}
	e.commit(target, fmt.Sprintf("rollover %s %s", agent, day))
	return true, fmt.Sprintf("%s: rolled %s -> %s.md (carried %d in progress, %d blocked)",
		agent, filepath.Base(last), day, len(carried.Items("in_progress")), len(carried.Items("blocked")))
}

// commit puts a published digest in the home's git when the home is a repository, so generations and rollback come for free. A failure
// leaves the file written.
func (e *Env) commit(file, message string) {
	if _, err := os.Stat(filepath.Join(e.Home, ".git")); err != nil {
		return
	}
	rel, err := filepath.Rel(e.Home, file)
	if err != nil {
		return
	}
	gitrun.Run(e.Home, "add", rel)
	gitrun.Run(e.Home, "commit", "-q", "-m", message)
}
