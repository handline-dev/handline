package reads

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

const (
	maxPRNumbers = 20
	prMissTTL    = time.Minute
	prCacheCap   = 5000
	prLookupWait = 20 * time.Second
)

var githubRemote = regexp.MustCompile(`^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$`)

// githubRepo is owner/repo of a GitHub remote url (https or ssh), "" for anything else.
func githubRepo(url string) string {
	m := githubRemote.FindStringSubmatch(strings.TrimSpace(url))
	if m == nil {
		return ""
	}
	return m[1] + "/" + m[2]
}

// originRepo is the GitHub repo of the origin of the worktree at path, "" when it has none or is not on GitHub.
func originRepo(path string) string {
	ok, out := git(path, "remote", "get-url", "origin")
	if !ok {
		return ""
	}
	return githubRepo(out)
}

// prCache remembers which numbers are pull requests of a repo: a hit for good (a PR does not stop being one), a miss until it expires.
type prCache struct {
	mu  sync.Mutex
	now func() time.Time
	m   map[string]prEntry
}

type prEntry struct {
	hit   bool
	until time.Time
}

func newPRCache() *prCache { return &prCache{now: time.Now, m: map[string]prEntry{}} }

func prKey(repo string, n int) string { return fmt.Sprintf("%s#%d", repo, n) }

// split sorts numbers into the known hits and the ones to ask GitHub about (unknown, or a miss that has expired).
func (c *prCache) split(repo string, numbers []int) (hits, ask []int) {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := c.now()
	for _, n := range numbers {
		e, ok := c.m[prKey(repo, n)]
		switch {
		case ok && e.hit:
			hits = append(hits, n)
		case ok && now.Before(e.until):
		default:
			ask = append(ask, n)
		}
	}
	return hits, ask
}

// record stores the answer for the numbers asked: found ones as hits, the rest as misses.
func (c *prCache) record(repo string, asked, found []int) {
	isPR := map[int]bool{}
	for _, n := range found {
		isPR[n] = true
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	now := c.now()
	if len(c.m)+len(asked) > prCacheCap {
		for k, e := range c.m {
			if !e.hit && !now.Before(e.until) {
				delete(c.m, k)
			}
		}
		if len(c.m)+len(asked) > prCacheCap {
			c.m = map[string]prEntry{}
		}
	}
	for _, n := range asked {
		c.m[prKey(repo, n)] = prEntry{hit: isPR[n], until: now.Add(prMissTTL)}
	}
}

// ghPulls asks GitHub, with the daemon's own gh login, which of numbers are pull requests of repo, in one call.
func ghPulls(ctx context.Context, repo string, numbers []int) ([]int, error) {
	owner, name, _ := strings.Cut(repo, "/")
	var q strings.Builder
	fmt.Fprintf(&q, "query{repository(owner:%q,name:%q){", owner, name)
	for _, n := range numbers {
		fmt.Fprintf(&q, "p%d:pullRequest(number:%d){number} ", n, n)
	}
	q.WriteString("}}")
	// gh exits non-zero when any alias is not a PR, but still prints the data: the answer is read from the output
	out, runErr := exec.CommandContext(ctx, "gh", "api", "graphql", "-f", "query="+q.String()).Output()
	var resp struct {
		Data struct {
			Repository map[string]*struct{ Number int } `json:"repository"`
		} `json:"data"`
		Errors []struct{ Type string } `json:"errors"`
	}
	if err := json.Unmarshal(out, &resp); err != nil || resp.Data.Repository == nil {
		if runErr == nil {
			runErr = fmt.Errorf("gh: no repository in the answer")
		}
		return nil, runErr
	}
	for _, e := range resp.Errors {
		if e.Type != "NOT_FOUND" {
			return nil, fmt.Errorf("gh: %s", e.Type)
		}
	}
	var found []int
	for _, p := range resp.Data.Repository {
		if p != nil {
			found = append(found, p.Number)
		}
	}
	return found, nil
}

// prExists is pr.exists: which of the numbers are pull requests in the GitHub repo of the agent's worktree.
func (e *Env) prExists(_ *rpc.Conn, params map[string]any) (any, error) {
	name, err := e.named(params)
	if err != nil {
		return nil, err
	}
	raw, ok := params["numbers"].([]any)
	if !ok || len(raw) > maxPRNumbers {
		return nil, badParam(fmt.Sprintf("numbers is a list of at most %d integers", maxPRNumbers), "numbers")
	}
	seen := map[int]bool{}
	var numbers []int
	for _, v := range raw {
		f, isNum := v.(float64)
		if !isNum || f != float64(int(f)) || f < 1 {
			return nil, badParam("numbers holds positive integers", "numbers")
		}
		if n := int(f); !seen[n] {
			seen[n] = true
			numbers = append(numbers, n)
		}
	}
	found := []int{}
	row, err := queryOne(e.DB, "SELECT worktree FROM agent_state WHERE agent = ?", name)
	if err != nil {
		return nil, err
	}
	repo := ""
	if path := row.str("worktree"); path != "" {
		repo = originRepo(path)
	}
	if repo == "" {
		return map[string]any{"repo": nil, "found": found}, nil
	}
	hits, ask := e.prs.split(repo, numbers)
	found = append(found, hits...)
	if len(ask) > 0 {
		lookup := e.PullRequests
		if lookup == nil {
			lookup = ghPulls
		}
		ctx, cancel := context.WithTimeout(context.Background(), prLookupWait)
		defer cancel()
		if got, err := lookup(ctx, repo, ask); err == nil {
			e.prs.record(repo, ask, got)
			found = append(found, got...)
		}
	}
	sort.Ints(found)
	return map[string]any{"repo": repo, "found": found}, nil
}
