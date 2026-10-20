package hrwrites

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/worktreeprune"
)

// pruneMemberWorktrees removes each worktree of a member who left the team that is done with, by the rule tools/prune_worktrees.py
// uses; any other stays, and says why.
func (e *Env) pruneMemberWorktrees(name string, worktrees []string) string {
	if len(worktrees) == 0 {
		return ""
	}
	others, err := worktreeprune.AgentWorktrees(e.DB, name)
	if err != nil {
		return fmt.Sprintf("Its worktrees were left: the other agents' could not be read (%v).", err)
	}
	var said []string
	for _, path := range worktrees {
		wt, found := find(worktreeprune.List(path), path)
		if !found {
			continue // gone already, or not a worktree git knows
		}
		if why := worktreeprune.WhyKept(wt, others, ""); why != "" {
			said = append(said, fmt.Sprintf("%s stays: %s", path, why))
		} else if ok, why := worktreeprune.Remove(path); ok {
			said = append(said, path+" is removed")
		} else {
			said = append(said, fmt.Sprintf("%s stays: %s", path, why))
		}
	}
	if len(said) == 0 {
		return ""
	}
	return " Its worktrees: " + strings.Join(said, "; ") + "."
}

func find(all []worktreeprune.Worktree, path string) (worktreeprune.Worktree, bool) {
	for _, wt := range all {
		if worktreeprune.Real(wt.Path) == worktreeprune.Real(path) {
			return wt, true
		}
	}
	return worktreeprune.Worktree{}, false
}
