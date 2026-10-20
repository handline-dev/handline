// Package worktreeprune is the one rule for which worktrees are done with (tools/prune_worktrees.py runs it too): a worktree goes only
// when its branch is merged into main, it has no uncommitted or untracked file, and no agent works in it. Main's, a locked one and one
// on no branch stay. Removing one leaves its branch: `git branch -d` takes that.
package worktreeprune

import (
	"encoding/json"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
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
		return wt.Branch + " is not merged into main"
	}
	if st := gitrun.Run(wt.Path, "status", "--porcelain"); st.RC != 0 || strings.TrimSpace(st.Stdout) != "" {
		return "uncommitted or untracked files"
	}
	return ""
}

// Remove removes the worktree at path, never by force: git refuses one that became dirty since it was judged.
func Remove(path string) (ok bool, why string) {
	r := gitrun.Run(path, "worktree", "remove", path)
	if r.RC != 0 {
		return false, strings.TrimSpace(r.Stderr)
	}
	return true, ""
}
