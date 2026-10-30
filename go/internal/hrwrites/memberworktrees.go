package hrwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/worktreeprune"
)

// pruneMemberWorktrees removes each worktree an agent left for another that is done with, by the rule tools/prune_worktrees.py uses,
// and its branch with it (git branch -d); any other stays and says why. One that holds work is put to the founder as a task, and goes
// only by their answer (chatwrites.DecideCard). A retired agent's worktrees go before it does (leaving, clear).
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
		default:
			said = append(said, fmt.Sprintf("%s stays: %s%s", path, why, e.askAbout(name, wt, why)))
		}
	}
	if len(said) == 0 {
		return ""
	}
	return " Its worktrees: " + strings.Join(said, "; ") + "."
}

// askAbout puts a worktree that holds work to the founder, and says so for the retire reply; "" when there is nothing to ask. The task
// says in plain words what would be lost; its answers are worktreeprune.Keep, Save and Force, which the app shows as buttons for the Tag.
func (e *Env) askAbout(member string, wt worktreeprune.Worktree, why string) string {
	if e.Ask == nil || !worktreeprune.HoldsWork(why) {
		return ""
	}
	_, files, commits := worktreeprune.Unsaved(wt.Path)
	title := fmt.Sprintf("Remove %s's worktree?", member)
	body := fmt.Sprintf("%s no longer works in this worktree, and it holds work that is not on main: %s changed, %s not on main.\n\n",
		member, plural(files, "file"), plural(commits, "commit")) + worktreeprune.Describe(wt) + "\n\nMember: " + member + `

Answer with one word (a comment or a chat message does nothing):
keep   Keep the worktree: it stays as it is
save   Save to a branch, then delete: what is left is committed to rescue/` + member + `-<date>, then the worktree goes
force  Force delete: it goes with what it holds; its commits stay on its branch

Nothing is removed until you answer.`
	tid, err := e.Ask(title, body, worktreeprune.Tag)
	if err != nil {
		e.logf("  asking about %s: %v", wt.Path, err)
		return ""
	}
	return fmt.Sprintf(" (asked the founder in T%d)", tid)
}

func plural(n int, noun string) string {
	if n == 1 {
		return "1 " + noun
	}
	return fmt.Sprintf("%d %ss", n, noun)
}

// leftSeen is the cursor of the last WorktreeLeft event SweepLeftWorktrees judged.
const leftSeen = "worktree_left_seen"

// SweepLeftWorktrees judges each worktree an agent left for another (db.WorktreeLeft, written when it launched in a new one), as a retired
// agent's: merged and clean, it goes with its branch; holding work, it is put to the founder. One any agent still records is left alone.
func (e *Env) SweepLeftWorktrees() error {
	seen, err := db.Cursor(e.DB, leftSeen)
	if err != nil {
		return err
	}
	rows, err := e.DB.Query("SELECT id, agent, json_extract(payload, '$.path') FROM events WHERE kind = ? AND id > ? ORDER BY id", db.WorktreeLeft, seen)
	if err != nil {
		return err
	}
	type left struct {
		id          int64
		agent, path string
	}
	var all []left
	for rows.Next() {
		var l left
		var path sql.NullString
		if err := rows.Scan(&l.id, &l.agent, &path); err != nil {
			rows.Close()
			return err
		}
		l.path = path.String
		if _, p, ok := strings.Cut(l.path, "::"); ok {
			l.path = p
		}
		all = append(all, l)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	for _, l := range all {
		recorded, err := worktreeprune.AgentWorktrees(e.DB, "")
		if err != nil {
			return err
		}
		if l.path != "" && !recorded[worktreeprune.Real(l.path)] {
			if said := e.pruneMemberWorktrees(l.agent, []string{l.path}); said != "" {
				e.logf("%s left a worktree:%s", l.agent, said)
			}
		}
		if err := db.SetCursor(e.DB, leftSeen, l.id); err != nil {
			return err
		}
	}
	return nil
}
