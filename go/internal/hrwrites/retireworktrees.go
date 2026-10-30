package hrwrites

import (
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/worktreeprune"
)

// leave is one worktree a deleted agent leaves, and how it goes: "" removed (with its branch when that is merged), or the founder's
// answer worktreeprune.Save or Force.
type leave struct {
	wt   worktreeprune.Worktree
	repo string // the repository's own checkout, where its branch is deleted
	how  string
}

// leaving is how each of agent's worktrees goes when it is deleted, decided before anything changes: an agent is deleted only once
// its worktrees are gone, so none is left behind. One that is gone already, the repository's own checkout and one another agent also
// works in are not touched. refused says, in plain words, why one cannot go; then nothing is done. choice is the founder's answer for a
// worktree that holds work.
func (e *Env) leaving(agent string, mine []string, choice string) (plans []leave, refused string, err error) {
	if len(mine) == 0 {
		return nil, "", nil
	}
	others, err := worktreeprune.AgentWorktrees(e.DB, agent)
	if err != nil {
		return nil, "", err
	}
	for _, path := range mine {
		all := worktreeprune.List(path)
		wt, found := worktreeprune.Find(all, path)
		if !found || worktreeprune.Real(all[0].Path) == worktreeprune.Real(path) {
			continue
		}
		why := worktreeprune.WhyKept(wt, others, "")
		switch {
		case why == "":
			plans = append(plans, leave{wt, all[0].Path, ""})
		case why == "not a checkout" || why == "an agent works in it":
		case worktreeprune.HoldsWork(why) && (choice == worktreeprune.Save || choice == worktreeprune.Force):
			plans = append(plans, leave{wt, all[0].Path, choice})
		case worktreeprune.HoldsWork(why) && choice == worktreeprune.Keep:
			return nil, fmt.Sprintf("%s holds work (%s) and is kept, as asked, so %s is not deleted.", path, why, agent), nil
		case worktreeprune.HoldsWork(why):
			return nil, fmt.Sprintf("%s holds work (%s): answer save or force for it, or %s is not deleted.", path, why, agent), nil
		case choice == worktreeprune.Force:
			plans = append(plans, leave{wt, all[0].Path, choice})
		default:
			return nil, fmt.Sprintf("%s cannot be removed (%s), so %s is not deleted; force removes it.", path, why, agent), nil
		}
	}
	return plans, "", nil
}

// clear removes the worktrees leaving planned, in order, and says what went. refused is why one did not go: the agent is then not
// deleted, and those removed before it stay removed (each was merged and clean, or went by the founder's answer).
func (e *Env) clear(agent string, plans []leave) (said, refused string) {
	var done []string
	for _, l := range plans {
		path := l.wt.Path
		switch l.how {
		case worktreeprune.Save:
			branch, err := worktreeprune.Rescue(path, agent, time.Now())
			if err != nil {
				return "", e.notCleared(agent, path, err.Error(), done)
			}
			done = append(done, path+" is removed; what it held is on "+branch)
		case worktreeprune.Force:
			if ok, why := worktreeprune.RemoveForce(path); !ok {
				return "", e.notCleared(agent, path, why, done)
			}
			done = append(done, path+" is removed with what it held")
		default:
			if ok, why := worktreeprune.Remove(path); !ok {
				return "", e.notCleared(agent, path, why, done)
			}
			if worktreeprune.DeleteMergedBranch(l.repo, l.wt.Branch) {
				done = append(done, path+" is removed with its branch "+l.wt.Branch)
			} else {
				done = append(done, path+" is removed")
			}
		}
	}
	if len(done) == 0 {
		return "", ""
	}
	return " Its worktrees: " + strings.Join(done, "; ") + ".", ""
}

func (e *Env) notCleared(agent, path, why string, done []string) string {
	s := fmt.Sprintf("%s could not be removed (%s), so %s is not deleted.", path, why, agent)
	if len(done) > 0 {
		s += " Removed before it: " + strings.Join(done, "; ") + "."
	}
	return s
}
