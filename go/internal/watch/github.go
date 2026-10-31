package watch

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

// NoCIWait is how long a PR with no checks at all is waited on, from its head commit or the watch's start, whichever is later, before it
// is taken to have no CI.
const NoCIWait = 10 * time.Minute

// GitHub watches a pull request's CI (ci) or its close (closed), or an issue being finished (done), through the gh CLI's own login: handline
// never sees a token. A target is owner/name#N.
type GitHub struct {
	// Run runs gh with args and answers its standard output; nil runs the gh CLI. Now is the clock; nil is time.Now.
	Run func(ctx context.Context, args ...string) ([]byte, error)
	Now func() time.Time
}

var githubTarget = regexp.MustCompile(`^([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#([1-9][0-9]*)$`)

func (*GitHub) Untils() []string { return []string{"ci", "closed", "done"} }

func (g *GitHub) Ready(ctx context.Context) (bool, string) {
	if _, err := g.gh(ctx, "auth", "status"); err != nil {
		if errors.Is(err, exec.ErrNotFound) {
			return false, "the gh CLI is not installed"
		}
		return false, "gh is not logged in: run gh auth login"
	}
	return true, ""
}

// Parse takes ci and closed to be about a pull request and done about an issue; GitHub numbers them together, so which one #N is shows
// only when it is read, and a number of the other kind reads as gone.
func (*GitHub) Parse(target, until string) (string, string, error) {
	m := githubTarget.FindStringSubmatch(target)
	if m == nil {
		return "", "", fmt.Errorf("a github target is owner/name#N, not %q", target)
	}
	switch until {
	case "ci":
		return target, "PR #" + m[2] + " CI", nil
	case "closed":
		return target, "PR #" + m[2] + " to close", nil
	}
	return target, "issue #" + m[2] + " done", nil
}

func (g *GitHub) Check(ctx context.Context, target, until string, since time.Time) (Verdict, string, error) {
	m := githubTarget.FindStringSubmatch(target)
	if m == nil {
		return "", "", fmt.Errorf("a github target is owner/name#N, not %q", target)
	}
	repo, n := m[1], m[2]
	kind, fields := "pr", "state,mergeCommit,statusCheckRollup,commits"
	if until == "done" {
		kind, fields = "issue", "state,stateReason"
	}
	out, err := g.gh(ctx, kind, "view", n, "-R", repo, "--json", fields)
	if err != nil {
		if gone(err) {
			return Gone, err.Error(), nil
		}
		return "", "", err
	}
	switch until {
	case "ci":
		return ciVerdict(out, since, g.now())
	case "closed":
		return closedVerdict(out)
	}
	return doneVerdict(out)
}

type ghCheck struct {
	Typename string `json:"__typename"`
	Name     string
	// WorkflowName tells apart checks of one name in different workflows; a rerun stays in its workflow.
	WorkflowName string `json:"workflowName"`
	Context      string
	Status       string
	Conclusion   string
	State        string
	StartedAt    string
}

// waiting is a check that has not finished: a check run not COMPLETED, or a commit status PENDING or EXPECTED.
func (c ghCheck) waiting() bool {
	if c.Typename == "StatusContext" {
		return c.State == "PENDING" || c.State == "EXPECTED"
	}
	return c.Status != "COMPLETED"
}

// started orders runs of one check. A run still waiting that has no real start time yet (queued, or just rerun) is the newest of all.
func (c ghCheck) started() string {
	if c.waiting() && (c.StartedAt == "" || strings.HasPrefix(c.StartedAt, "0001-")) {
		return "\xff"
	}
	return c.StartedAt
}

// latest keeps one entry per check, the one that started last (a later entry on a tie), in the order the checks first appear. A check that was
// cancelled or failed and run again leaves its old run in the rollup; only the newest says where the check stands.
func latest(rollup []ghCheck) []ghCheck {
	var out []ghCheck
	at := map[string]int{}
	for _, c := range rollup {
		key := c.Typename + "\x00" + c.WorkflowName + "\x00" + c.Name + c.Context
		i, seen := at[key]
		switch {
		case !seen:
			at[key] = len(out)
			out = append(out, c)
		case c.started() >= out[i].started():
			out[i] = c
		}
	}
	return out
}

// ciVerdict reads `gh pr view --json state,mergeCommit,statusCheckRollup,commits`. A check run is waiting until it completes and ok when
// it concludes SUCCESS, NEUTRAL or SKIPPED; a commit status is waiting while PENDING or EXPECTED and ok when SUCCESS. Only the latest run
// of each check counts. Every check ok is passed and any other end is failed, once none waits. A PR that closes while its CI still waits
// is failed: that CI will not finish. A PR with no checks NoCIWait after its head commit and the watch's start has no CI, and is passed.
func ciVerdict(out []byte, since, now time.Time) (Verdict, string, error) {
	var pr struct {
		State   string
		Rollup  []ghCheck `json:"statusCheckRollup"`
		Commits []struct{ CommittedDate time.Time }
	}
	if err := json.Unmarshal(out, &pr); err != nil {
		return "", "", err
	}
	closed := pr.State == "MERGED" || pr.State == "CLOSED"
	if len(pr.Rollup) == 0 {
		switch {
		case closed:
			return Failed, "closed before CI", nil
		case len(pr.Commits) > 0:
			if head := pr.Commits[len(pr.Commits)-1].CommittedDate; head.After(since) {
				since = head
			}
			if now.Sub(since) >= NoCIWait {
				return Passed, "no CI on this PR", nil
			}
		}
		return Waiting, "", nil
	}
	var failed []string
	for _, c := range latest(pr.Rollup) {
		ok, name := c.Conclusion == "SUCCESS" || c.Conclusion == "NEUTRAL" || c.Conclusion == "SKIPPED", c.Name
		if c.Typename == "StatusContext" {
			ok, name = c.State == "SUCCESS", c.Context
		}
		if c.waiting() {
			if closed {
				return Failed, "closed before CI", nil
			}
			return Waiting, "", nil
		}
		if !ok {
			failed = append(failed, name)
		}
	}
	if len(failed) > 0 {
		return Failed, "ci failed: " + strings.Join(failed, ", "), nil
	}
	return Passed, "ci passed", nil
}

func closedVerdict(out []byte) (Verdict, string, error) {
	var pr struct {
		State       string
		MergeCommit *struct{ Oid string }
	}
	if err := json.Unmarshal(out, &pr); err != nil {
		return "", "", err
	}
	switch pr.State {
	case "MERGED":
		if pr.MergeCommit == nil {
			return Passed, "merged", nil
		}
		return Passed, "merged " + pr.MergeCommit.Oid, nil
	case "CLOSED":
		return Failed, "closed unmerged", nil
	}
	return Waiting, "", nil
}

// doneVerdict is an issue closed as completed (passed) or for any other reason, such as not planned (failed).
func doneVerdict(out []byte) (Verdict, string, error) {
	var is struct{ State, StateReason string }
	if err := json.Unmarshal(out, &is); err != nil {
		return "", "", err
	}
	if is.State != "CLOSED" {
		return Waiting, "", nil
	}
	if is.StateReason == "COMPLETED" || is.StateReason == "" {
		return Passed, "closed as completed", nil
	}
	return Failed, "closed as " + strings.ToLower(strings.ReplaceAll(is.StateReason, "_", " ")), nil
}

// gone is a gh error that says the repo or the number is not there, or not to be seen by this login: GitHub answers both the same.
func gone(err error) bool {
	s := err.Error()
	return strings.Contains(s, "Could not resolve to a") || strings.Contains(s, "HTTP 404")
}

func (g *GitHub) gh(ctx context.Context, args ...string) ([]byte, error) {
	if g.Run != nil {
		return g.Run(ctx, args...)
	}
	out, err := exec.CommandContext(ctx, "gh", args...).Output()
	var exit *exec.ExitError
	if errors.As(err, &exit) && len(exit.Stderr) > 0 {
		return nil, fmt.Errorf("gh %s: %s", args[0], strings.TrimSpace(string(exit.Stderr)))
	}
	return out, err
}

func (g *GitHub) now() time.Time {
	if g.Now != nil {
		return g.Now()
	}
	return time.Now()
}
