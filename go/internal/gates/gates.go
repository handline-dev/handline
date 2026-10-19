// Package gates opens the gate tasks: a task owned by db.Gate watches a GitHub PR's CI or its close, and is marked done, in fixed words,
// when that changes or its deadline passes. The tasks it blocks then come back to their holders by the ordinary unblock; what follows is
// theirs to decide.
package gates

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/taskwrites"
)

// EveryS is how often the gates are looked at; FetchWait is how long one PR's read may take.
const (
	EveryS    = 60
	FetchWait = 20 * time.Second
)

// PR is what a gate reads of its pull request: State OPEN, MERGED or CLOSED, the merge commit, and each check with how it ended
// (Pending while it runs).
type PR struct {
	State       string
	MergeCommit string
	Checks      []Check
}

// Check is one CI check of a PR's head commit.
type Check struct {
	Name             string
	Pending, Success bool
}

// Sweep is the gate pass, run after each tick (tick.NamedSweep); it looks at most every EveryS.
type Sweep struct {
	DB   *sql.DB
	Home string
	Tell taskwrites.Teller
	Log  func(format string, args ...any)
	// Fetch reads a PR; nil is the gh CLI (FetchGH).
	Fetch func(ctx context.Context, repo string, pr int64) (*PR, error)
	last  int64
}

// Run opens every gate whose state changed or whose deadline passed.
func (s *Sweep) Run(_ db.Q, now int64) error {
	if now-s.last < EveryS {
		return nil
	}
	s.last = now
	ids, watches, deadlines, err := db.OpenGates(s.DB)
	if err != nil {
		return err
	}
	fetch := s.Fetch
	if fetch == nil {
		fetch = FetchGH
	}
	for i, tid := range ids {
		w := watches[i]
		evidence, result := "", ""
		if deadlines[i] != 0 && now >= deadlines[i] {
			evidence, result = "timed out", "timed_out"
		} else {
			ctx, cancel := context.WithTimeout(context.Background(), FetchWait)
			pr, err := fetch(ctx, w.Repo, w.PR)
			cancel()
			if err != nil {
				s.logf("  T%d: %s %s could not be read: %v", tid, w.Repo, w.Said(), err)
				continue
			}
			evidence, result = Opens(w, pr)
		}
		if evidence == "" {
			continue
		}
		if err := s.open(tid, evidence, result); err != nil {
			s.logf("  T%d: the gate could not be opened: %v", tid, err)
			continue
		}
		s.logf("  T%d: gate on %s %s opened - %s", tid, w.Repo, w.Said(), evidence)
	}
	return nil
}

// Opens is the evidence and result a gate opens with on what its PR shows, "" while it keeps watching. A PR that closes opens a gate on
// its CI too: that CI will not finish.
func Opens(w db.Watch, pr *PR) (evidence, result string) {
	switch pr.State {
	case "MERGED":
		return "merged " + pr.MergeCommit, "merged"
	case "CLOSED":
		return "closed unmerged", "closed"
	}
	if w.On != "ci" || len(pr.Checks) == 0 {
		return "", ""
	}
	var failed []string
	for _, c := range pr.Checks {
		if c.Pending {
			return "", ""
		}
		if !c.Success {
			failed = append(failed, c.Name)
		}
	}
	if len(failed) > 0 {
		return "ci failed: " + strings.Join(failed, ", "), "failed"
	}
	return "ci passed", "passed"
}

// open marks gate tid done with evidence, as its owner, and keeps the result; the tasks it blocks are told after the commit.
func (s *Sweep) open(tid int64, evidence, result string) error {
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	done, err := taskwrites.MarkDone(tx, s.Home, db.Gate, taskwrites.DoneIn{Task: tid, Words: evidence})
	if err != nil {
		return err
	}
	if err := db.SetWatchResult(tx, tid, result); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	if s.Tell != nil {
		s.Tell.Done(tid, db.Gate, done.Owner, done.Told, done.Forget, done.Finished)
	}
	return nil
}

func (s *Sweep) logf(format string, args ...any) {
	if s.Log != nil {
		s.Log(format, args...)
	}
}

// FetchGH reads a PR with `gh pr view`.
func FetchGH(ctx context.Context, repo string, pr int64) (*PR, error) {
	out, err := exec.CommandContext(ctx, "gh", "pr", "view", fmt.Sprint(pr), "-R", repo, "--json", "state,mergeCommit,statusCheckRollup").Output()
	if err != nil {
		return nil, err
	}
	return ParseGH(out)
}

// ParseGH is a PR from `gh pr view --json state,mergeCommit,statusCheckRollup`: a check run is pending until it completes and a success
// when it concludes SUCCESS, NEUTRAL or SKIPPED; a commit status is pending while PENDING or EXPECTED and a success when SUCCESS.
func ParseGH(out []byte) (*PR, error) {
	var raw struct {
		State       string
		MergeCommit *struct{ Oid string }
		Rollup      []struct {
			Typename   string `json:"__typename"`
			Name       string
			Context    string
			Status     string
			Conclusion string
			State      string
		} `json:"statusCheckRollup"`
	}
	if err := json.Unmarshal(out, &raw); err != nil {
		return nil, err
	}
	pr := &PR{State: raw.State}
	if raw.MergeCommit != nil {
		pr.MergeCommit = raw.MergeCommit.Oid
	}
	for _, c := range raw.Rollup {
		if c.Typename == "StatusContext" {
			pr.Checks = append(pr.Checks, Check{Name: c.Context, Pending: c.State == "PENDING" || c.State == "EXPECTED", Success: c.State == "SUCCESS"})
			continue
		}
		ok := c.Conclusion == "SUCCESS" || c.Conclusion == "NEUTRAL" || c.Conclusion == "SKIPPED"
		pr.Checks = append(pr.Checks, Check{Name: c.Name, Pending: c.Status != "COMPLETED", Success: ok})
	}
	return pr, nil
}
