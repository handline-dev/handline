package hrwrites

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/worktreeprune"
)

// pruneMemberWorktrees removes each worktree of a member who left the team that is done with, by the rule tools/prune_worktrees.py
// uses; any other stays and says why. One that holds work (commits not on main, files not committed) is also put to the founder as a
// task, and goes only by their answer (chatwrites.DecideCard).
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
			said = append(said, fmt.Sprintf("%s stays: %s%s", path, why, e.askAbout(name, wt, why)))
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

// askAbout puts a worktree that holds work to the founder, and says so for the retire reply; "" when there is nothing to ask.
func (e *Env) askAbout(member string, wt worktreeprune.Worktree, why string) string {
	if e.Ask == nil || !worktreeprune.HoldsWork(why) {
		return ""
	}
	title := fmt.Sprintf("Remove %s's worktree?", member)
	body := worktreeprune.Describe(wt) + "\n\nMember: " + member + `

Reply with the number (answer this task):
1  keep a copy, then remove: what is left is committed to a rescue/` + member + `-<date> branch, then the worktree goes
2 yes  remove anyway: uncommitted files are lost (commits stay on the branch); "2" alone asks again
3  leave it: the worktree stays, the member is gone

Nothing is removed until you answer.`
	tid, err := e.Ask(title, body, worktreeprune.Tag)
	if err != nil {
		e.logf("  asking about %s: %v", wt.Path, err)
		return ""
	}
	return fmt.Sprintf(" (asked the founder in T%d)", tid)
}
