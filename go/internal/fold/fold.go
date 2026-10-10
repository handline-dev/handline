// Package fold is the digest upkeep that is not a decision: the midnight rollover, which carries yesterday's open items into
// today's digest with no model, and agent.fold, which runs `hl fold` as a subprocess (handline/session/fold.py). The daemon makes no
// model call of its own: the one call the system has stays in that command.
package fold

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/textsafe"
)

// Timeout is how long a fold may run: fold.FOLD_TIMEOUT_S and the 30 seconds control.py allows beside it.
const Timeout = 210 * time.Second

// Env is what the two need.
type Env struct {
	Home string
	// Cmd is the argv that runs hl; "fold <agent>" is appended. Empty runs `hl` from the path.
	Cmd     []string
	Timeout time.Duration // zero is Timeout
	Today   func() string
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

// Fold is control.fold: `hl fold <agent>` run to the end, said as the verb says it. ok is false when the fold was rejected.
func (e *Env) Fold(agent string) (ok bool, said string) {
	limit := e.Timeout
	if limit == 0 {
		limit = Timeout
	}
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	argv := append(append([]string{}, e.Cmd...), "fold", agent)
	if len(e.Cmd) == 0 {
		argv = []string{"hl", "fold", agent}
	}
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Env = append(os.Environ(), "HANDLINE_HOME="+e.Home, "OCP_HOME="+e.Home)
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	err := cmd.Run()
	msg := strings.TrimSpace(textsafe.Text(out.String()))
	switch {
	case ctx.Err() != nil:
		return false, fmt.Sprintf("%s: fold rejected - timed out after %s", agent, limit)
	case err != nil && msg == "":
		return false, fmt.Sprintf("%s: fold rejected - %v", agent, err)
	case err != nil:
		return false, fmt.Sprintf("%s: fold rejected - %s", agent, msg)
	}
	return true, msg
}
