package reads

import (
	"context"
	"encoding/json"
	"os/exec"
	"sync"
	"time"
)

const (
	branchPRTTL    = time.Minute
	branchPRCap    = 2000
	branchPRWait   = 20 * time.Second
	branchPRAtOnce = 8
)

// branchPRs remembers each branch's pull request, or that it has none, for branchPRTTL: an answer gh could not give is kept the same
// way, as no pull request, so a gh that is down is asked once a minute, not once a read.
type branchPRs struct {
	mu  sync.Mutex
	now func() time.Time
	m   map[string]branchPR
}

type branchPR struct {
	pr    map[string]any
	until time.Time
}

func newBranchPRs() *branchPRs { return &branchPRs{now: time.Now, m: map[string]branchPR{}} }

func (c *branchPRs) get(key string) (map[string]any, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	e, ok := c.m[key]
	return e.pr, ok && c.now().Before(e.until)
}

func (c *branchPRs) put(key string, pr map[string]any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.m) >= branchPRCap {
		c.m = map[string]branchPR{}
	}
	c.m[key] = branchPR{pr: pr, until: c.now().Add(branchPRTTL)}
}

// ghPull is one pull request as `gh pr list --json` gives it.
type ghPull struct {
	Number         int
	URL            string
	State          string
	IsDraft        bool
	ReviewDecision string
	Checks         []struct {
		Conclusion string
		State      string
	} `json:"statusCheckRollup"`
}

// pullVerdict is the worktrees.list `pr` of a pull request: its number, url and what holds it.
func pullVerdict(p ghPull) map[string]any {
	failing := 0
	for _, c := range p.Checks {
		switch {
		case c.Conclusion == "FAILURE", c.Conclusion == "TIMED_OUT", c.Conclusion == "STARTUP_FAILURE", c.State == "FAILURE", c.State == "ERROR":
			failing++
		}
	}
	out := map[string]any{"number": p.Number, "url": p.URL}
	switch {
	case p.State == "MERGED":
		out["verdict"] = "merged"
	case p.IsDraft:
		out["verdict"] = "draft"
	case failing > 0:
		out["verdict"], out["failing"] = "checks_failing", failing
	case p.ReviewDecision == "CHANGES_REQUESTED":
		out["verdict"] = "changes_requested"
	case p.ReviewDecision == "REVIEW_REQUIRED":
		out["verdict"] = "waiting_review"
	default:
		out["verdict"] = "ready"
	}
	return out
}

// ghBranchPR asks GitHub, with the daemon's own gh login, for the pull request of branch in repo: the newest open one, else the newest
// merged one, else nil.
func ghBranchPR(ctx context.Context, repo, branch string) (map[string]any, error) {
	out, err := exec.CommandContext(ctx, "gh", "pr", "list", "-R", repo, "--head", branch, "--state", "all", "--limit", "10",
		"--json", "number,url,state,isDraft,reviewDecision,statusCheckRollup").Output()
	if err != nil {
		return nil, err
	}
	var pulls []ghPull
	if err := json.Unmarshal(out, &pulls); err != nil {
		return nil, err
	}
	for _, state := range []string{"OPEN", "MERGED"} {
		for _, p := range pulls {
			if p.State == state {
				return pullVerdict(p), nil
			}
		}
	}
	return nil, nil
}

// attachPRs sets "pr" on each worktree row (as worktrees.list builds them): its branch's pull request, nil for none, for a branch that is
// its base, for a repo not on GitHub and for a gh that cannot answer. Lookups run a few at a time under one wait.
func (e *Env) attachPRs(rows []map[string]any) {
	lookup := e.BranchPR
	if lookup == nil {
		lookup = ghBranchPR
	}
	ctx, cancel := context.WithTimeout(context.Background(), branchPRWait)
	defer cancel()
	var wg sync.WaitGroup
	slots := make(chan struct{}, branchPRAtOnce)
	for _, row := range rows {
		row["pr"] = nil
		branch, _ := row["branch"].(string)
		base, _ := row["base"].(string)
		path, _ := row["path"].(string)
		if branch == "" || branch == "HEAD" || branch == base {
			continue
		}
		wg.Add(1)
		slots <- struct{}{}
		go func() {
			defer func() { <-slots; wg.Done() }()
			ok, url := git(path, "remote", "get-url", "origin")
			repo := githubRepo(url)
			if !ok || repo == "" {
				return
			}
			key := repo + "\x00" + branch
			pr, hit := e.branchPRs.get(key)
			if !hit {
				got, err := lookup(ctx, repo, branch)
				if err != nil {
					got = nil
				}
				e.branchPRs.put(key, got)
				pr = got
			}
			if pr != nil { // a nil map in the row would not be a JSON null to every encoder
				row["pr"] = pr
			}
		}()
	}
	wg.Wait()
}
