package hrwrites

import (
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/worktreeprune"
)

// Answers to a worktree that holds work, as agent.retire takes them (worktree) and the founder's task offers them.
const (
	KeepWorktree  = "keep"  // it stays
	SaveWorktree  = "save"  // what is left goes to a rescue branch, then it is removed
	ForceWorktree = "force" // it is removed with whatever it holds
)

// pruneMemberWorktrees removes each worktree of a member who left the team that is done with, by the rule tools/prune_worktrees.py
// uses, and its branch with it (git branch -d); any other stays and says why. One that holds work goes by choice (KeepWorktree,
// SaveWorktree, ForceWorktree) when the founder gave one; with none it is put to the founder as a task, and goes only by their answer
// (chatwrites.DecideCard).
func (e *Env) pruneMemberWorktrees(name string, worktrees []string, choice string) string {
	if len(worktrees) == 0 {
		return ""
	}
	others, err := worktreeprune.AgentWorktrees(e.DB, name)
	if err != nil {
		return fmt.Sprintf("Its worktrees were left: the other agents' could not be read (%v).", err)
	}
	var said []string
	for _, path := range worktrees {
		all := worktreeprune.List(path)
		wt, found := worktreeprune.Find(all, path)
		if !found {
			continue // gone already, or not a worktree git knows
		}
		why := worktreeprune.WhyKept(wt, others, "")
		switch {
		case why == "":
			if ok, why := worktreeprune.Remove(path); !ok {
				said = append(said, fmt.Sprintf("%s stays: %s", path, why))
			} else if worktreeprune.DeleteMergedBranch(all[0].Path, wt.Branch) {
				said = append(said, path+" is removed with its branch "+wt.Branch)
			} else {
				said = append(said, path+" is removed")
			}
		case worktreeprune.HoldsWork(why) && choice != "":
			said = append(said, path+" "+e.carryOut(name, path, choice))
		default:
			said = append(said, fmt.Sprintf("%s stays: %s%s", path, why, e.askAbout(name, wt, why)))
		}
	}
	if len(said) == 0 {
		return ""
	}
	return " Its worktrees: " + strings.Join(said, "; ") + "."
}

// carryOut does the founder's choice for a worktree that holds work, and says what was done.
func (e *Env) carryOut(member, path, choice string) string {
	switch choice {
	case SaveWorktree:
		branch, err := worktreeprune.Rescue(path, member, time.Now())
		if err != nil {
			return "stays: " + err.Error()
		}
		return "is removed; what it held is on " + branch
	case ForceWorktree:
		if ok, why := worktreeprune.RemoveForce(path); !ok {
			return "stays: " + why
		}
		return "is removed with what it held"
	}
	return "stays, as asked"
}

// askAbout puts a worktree that holds work to the founder, and says so for the retire reply; "" when there is nothing to ask.
func (e *Env) askAbout(member string, wt worktreeprune.Worktree, why string) string {
	if e.Ask == nil || !worktreeprune.HoldsWork(why) {
		return ""
	}
	title := fmt.Sprintf("Remove %s's worktree?", member)
	body := worktreeprune.Describe(wt) + "\n\nMember: " + member + `

Close this task with the number as its answer (a comment or a chat message does nothing):
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
