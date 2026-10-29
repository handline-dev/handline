// Package worktreeprune is the one rule for which worktrees are done with (tools/prune_worktrees.py runs it too): a worktree goes only
// when its branch is merged into main, it has no uncommitted or untracked file, and no agent works in it. Main's, a locked one and one
// on no branch stay. Removing one leaves its branch: `git branch -d` takes that.
package worktreeprune

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

// Worktree is one entry of `git worktree list --porcelain`.
type Worktree struct {
	Path, Branch           string
	Bare, Prunable, Locked bool
}

// List is the worktrees of the repository at repo, its own checkout first; nil when git cannot say.
func List(repo string) []Worktree {
	r := gitrun.Run(repo, "worktree", "list", "--porcelain")
	if r.RC != 0 {
		return nil
	}
	var out []Worktree
	var cur *Worktree
	for _, line := range append(strings.Split(r.Stdout, "\n"), "") {
		if line == "" {
			if cur != nil {
				out = append(out, *cur)
			}
			cur = nil
			continue
		}
		key, value, _ := strings.Cut(line, " ")
		if cur == nil {
			cur = &Worktree{}
		}
		switch key {
		case "worktree":
			cur.Path = value
		case "branch":
			cur.Branch = strings.TrimPrefix(value, "refs/heads/")
		case "bare":
			cur.Bare = true
		case "prunable":
			cur.Prunable = true
		case "locked":
			cur.Locked = true
		}
	}
	return out
}

// Real is path with its links resolved, as the paths are compared; path itself when it cannot be.
func Real(path string) string {
	if r, err := filepath.EvalSymlinks(path); err == nil {
		return r
	}
	return filepath.Clean(path)
}

// AgentWorktrees are the real paths of every worktree an agent records (agent_state.worktree and extra_worktrees), but those of skip.
func AgentWorktrees(q db.Q, skip string) (map[string]bool, error) {
	out := map[string]bool{}
	err := recorded(q, "SELECT agent, COALESCE(worktree, ''), COALESCE(extra_worktrees, '') FROM agent_state", func(agent, path string) {
		if agent != skip {
			out[path] = true
		}
	})
	return out, err
}

// Of are the real paths of the worktrees agent records.
func Of(q db.Q, agent string) ([]string, error) {
	var out []string
	err := recorded(q, "SELECT agent, COALESCE(worktree, ''), COALESCE(extra_worktrees, '') FROM agent_state WHERE agent = ?", func(_, path string) {
		out = append(out, path)
	}, agent)
	return out, err
}

// recorded hands each worktree path the rows of query record to each, as a real path. `hl launch` records <repo id>::<path>.
func recorded(q db.Q, query string, each func(agent, path string), args ...any) error {
	rows, err := q.Query(query, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var agent, first, extra string
		if err := rows.Scan(&agent, &first, &extra); err != nil {
			return err
		}
		paths := []string{first}
		if extra != "" {
			var more []string
			if err := json.Unmarshal([]byte(extra), &more); err != nil {
				return err
			}
			paths = append(paths, more...)
		}
		for _, p := range paths {
			if p == "" {
				continue
			}
			if _, path, ok := strings.Cut(p, "::"); ok {
				p = path
			}
			each(agent, Real(p))
		}
	}
	return rows.Err()
}

// WhyKept is why the worktree stays, "" when it may be removed. agents are the paths agents work in (AgentWorktrees), here the
// worktree this runs from ("" for none).
func WhyKept(wt Worktree, agents map[string]bool, here string) string {
	path := Real(wt.Path)
	switch {
	case wt.Bare || wt.Prunable:
		return "not a checkout"
	case here != "" && path == Real(here):
		return "this is run from it"
	case agents[path]:
		return "an agent works in it"
	case wt.Locked:
		return "locked"
	case wt.Branch == "":
		return "on no branch"
	case wt.Branch == "main":
		return "main"
	}
	if gitrun.Run(wt.Path, "merge-base", "--is-ancestor", wt.Branch, "main").RC != 0 {
		return wt.Branch + notMerged
	}
	if st := gitrun.Run(wt.Path, "status", "--porcelain"); st.RC != 0 || strings.TrimSpace(st.Stdout) != "" {
		return uncommitted
	}
	return ""
}

// Tag marks the founder's task that asks what to do with a retired member's worktree that holds work.
const Tag = "worktree-removal"

const (
	notMerged   = " is not merged into main"
	uncommitted = "uncommitted or untracked files"
)

// HoldsWork is whether why (WhyKept's) says the worktree holds work nobody else has: commits not on main, or files not committed.
func HoldsWork(why string) bool { return why == uncommitted || strings.HasSuffix(why, notMerged) }

// Describe is what a person needs to judge a worktree that holds work: its branch's commits not on main (up to 5 subjects), its changed
// and untracked files (up to 10) and when it last committed.
func Describe(wt Worktree) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Path: %s\nBranch: %s\n", wt.Path, wt.Branch)
	if r := gitrun.Run(wt.Path, "log", "main..HEAD", "--format=%s"); r.RC == 0 {
		b.WriteString(lines("Commits not on main", r.Stdout, 5))
	}
	if r := gitrun.Run(wt.Path, "status", "--porcelain"); r.RC == 0 {
		b.WriteString(lines("Changed or untracked files", r.Stdout, 10))
	}
	if r := gitrun.Run(wt.Path, "log", "-1", "--format=%cI"); r.RC == 0 {
		fmt.Fprintf(&b, "Last commit: %s", strings.TrimSpace(r.Stdout))
	}
	return strings.TrimSpace(b.String())
}

func lines(title, out string, most int) string {
	all := strings.Split(strings.TrimSpace(out), "\n")
	if len(all) == 1 && all[0] == "" {
		return ""
	}
	more := ""
	if len(all) > most {
		more = fmt.Sprintf("\n  ... and %d more", len(all)-most)
		all = all[:most]
	}
	return fmt.Sprintf("%s:\n  %s%s\n", title, strings.Join(all, "\n  "), more)
}

// Rescue keeps what the worktree holds, then removes it: the files not committed go in one commit on a new branch rescue/<member>-<date>
// (the branch the worktree was on stays as it is), so nothing is lost but what git ignores. It answers the branch.
func Rescue(path, member string, now time.Time) (branch string, err error) {
	branch = "rescue/" + member + "-" + now.Format("2006-01-02")
	for i := 2; gitrun.Run(path, "rev-parse", "--verify", "-q", "refs/heads/"+branch).RC == 0; i++ {
		branch = fmt.Sprintf("rescue/%s-%s-%d", member, now.Format("2006-01-02"), i)
	}
	steps := [][]string{
		{"checkout", "-q", "-b", branch},
		{"add", "-A"},
		{"-c", "user.name=handline", "-c", "user.email=handline@localhost", "commit", "-q", "--allow-empty", "-m", "rescue: what " + member + "'s worktree held when it was removed"},
	}
	for _, args := range steps {
		if r := gitrun.Run(path, args...); r.RC != 0 {
			return "", fmt.Errorf("git %s: %s", args[0], strings.TrimSpace(r.Stderr))
		}
	}
	if ok, why := Remove(path); !ok {
		return branch, fmt.Errorf("the rescue branch %s is made, but the worktree stays: %s", branch, why)
	}
	return branch, nil
}

// RemoveForce removes the worktree with whatever it holds; the branch it was on stays.
func RemoveForce(path string) (ok bool, why string) {
	r := gitrun.Run(path, "worktree", "remove", "--force", path)
	if r.RC != 0 {
		return false, strings.TrimSpace(r.Stderr)
	}
	return true, ""
}

// Remove removes the worktree at path, never by force: git refuses one that became dirty since it was judged.
func Remove(path string) (ok bool, why string) {
	r := gitrun.Run(path, "worktree", "remove", path)
	if r.RC != 0 {
		return false, strings.TrimSpace(r.Stderr)
	}
	return true, ""
}

// Repos are the repositories the agents were told to work in (team.yaml repo), each once, sorted.
func Repos(home string) []string {
	seen := map[string]bool{}
	var out []string
	roster := team.NewRoster(home)
	for _, name := range paths.ListAgents(home) {
		cfg, err := roster.Read(name)
		if err != nil {
			continue
		}
		if repo, _ := cfg["repo"].(string); repo != "" && !seen[repo] {
			if st, err := os.Stat(repo); err == nil && st.IsDir() {
				seen[repo] = true
				out = append(out, repo)
			}
		}
	}
	sort.Strings(out)
	return out
}

// Orphans are the worktrees of repo that no agent records (agents, from AgentWorktrees): left behind by an agent that went. The repo's own
// checkout, a locked worktree and one that is no checkout are never orphans.
func Orphans(repo string, agents map[string]bool) []Worktree {
	all := List(repo)
	var out []Worktree
	for i, wt := range all {
		if i == 0 || wt.Bare || wt.Prunable || wt.Locked || agents[Real(wt.Path)] {
			continue
		}
		out = append(out, wt)
	}
	return out
}

// DefaultBranch is the branch the repository at path merges into: origin's HEAD, else gitrun.IntegrationBranch.
func DefaultBranch(path string) string {
	if r := gitrun.Run(path, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"); r.RC == 0 && strings.TrimSpace(r.Stdout) != "" {
		return strings.TrimPrefix(strings.TrimSpace(r.Stdout), "origin/")
	}
	return gitrun.IntegrationBranch
}

// Unsaved is what the worktree holds that its repository's default branch does not: whether its HEAD is merged into it, its changed or
// untracked files and its commits not on it.
func Unsaved(path string) (merged bool, files, commits int) {
	merged, commits = Ahead(path)
	if r := gitrun.Run(path, "status", "--porcelain"); r.RC == 0 && strings.TrimSpace(r.Stdout) != "" {
		files = len(strings.Split(strings.TrimSpace(r.Stdout), "\n"))
	}
	return merged, files, commits
}

// Ahead is Unsaved without the files: whether the worktree's HEAD is merged into the default branch, and its commits not on it.
func Ahead(path string) (merged bool, commits int) {
	base := DefaultBranch(path)
	merged = gitrun.Run(path, "merge-base", "--is-ancestor", "HEAD", base).RC == 0
	if r := gitrun.Run(path, "rev-list", "--count", base+"..HEAD"); r.RC == 0 {
		commits, _ = strconv.Atoi(strings.TrimSpace(r.Stdout))
	}
	return merged, commits
}

// SizePrefix starts the meta name an orphan's measured size is kept under: the prefix, then its real path; the value is the bytes.
const SizePrefix = "worktree_size:"

// SizeEvery is how often the orphans' sizes are measured, in seconds: a walk of a worktree with its dependencies takes a while.
const SizeEvery = int64(3600)

// Size is the bytes the files under path take on disk, as du counts them.
func Size(path string) (int64, error) {
	out, err := exec.Command("du", "-sk", path).Output()
	if err != nil {
		return 0, err
	}
	fields := strings.Fields(string(out))
	if len(fields) == 0 {
		return 0, fmt.Errorf("du said nothing for %s", path)
	}
	kb, err := strconv.ParseInt(fields[0], 10, 64)
	return kb * 1024, err
}

// DeleteMergedBranch deletes branch in repo with `git branch -d`, never -D: git keeps a branch not merged, and false says so.
func DeleteMergedBranch(repo, branch string) bool {
	return branch != "" && branch != "main" && gitrun.Run(repo, "branch", "-d", branch).RC == 0
}

// FindOrphan is the orphan worktree at path among the repositories an agent works in, with its repository; false when path is none.
func FindOrphan(q db.Q, home, path string) (wt Worktree, repo string, ok bool, err error) {
	agents, err := AgentWorktrees(q, "")
	if err != nil {
		return Worktree{}, "", false, err
	}
	want := Real(path)
	for _, r := range Repos(home) {
		for _, o := range Orphans(r, agents) {
			if Real(o.Path) == want {
				return o, r, true, nil
			}
		}
	}
	return Worktree{}, "", false, nil
}

// Held is a worktree of an agent that holds work: its path, its changed or untracked files and its commits not on main.
type Held struct {
	Path           string
	Files, Commits int
}

// Holding are the worktrees agent records that hold work nobody else has (HoldsWork): what a retire must ask about before it goes.
func Holding(q db.Q, agent string) ([]Held, error) {
	mine, err := Of(q, agent)
	if err != nil {
		return nil, err
	}
	others, err := AgentWorktrees(q, agent)
	if err != nil {
		return nil, err
	}
	var out []Held
	for _, path := range mine {
		wt, ok := Find(List(path), path)
		if !ok || !HoldsWork(WhyKept(wt, others, "")) {
			continue
		}
		_, files, commits := Unsaved(path)
		out = append(out, Held{Path: path, Files: files, Commits: commits})
	}
	return out, nil
}

// Find is the worktree of all at path.
func Find(all []Worktree, path string) (Worktree, bool) {
	for _, wt := range all {
		if Real(wt.Path) == Real(path) {
			return wt, true
		}
	}
	return Worktree{}, false
}
