package adminwrites

import (
	"database/sql"
	"encoding/json"
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/worktreeprune"
)

const maxStatusRunes = 40

// worktree.set_status: the label worktrees.list shows in place of dirty or clean. It is one meta row per worktree, written here and
// nowhere else; the label changes nothing on disk. A path no agent works in is not_found, as for worktree.diff.
func (e *Env) worktreeSetStatus(c *rpc.Conn, p map[string]any) (any, error) {
	path, _ := p["path"].(string)
	if path == "" {
		return nil, badParam("path is a worktree path", "path")
	}
	status, _ := p["status"].(string)
	if n := len([]rune(status)); n < 1 || n > maxStatusRunes || textsafe.HasUnsafe(status) {
		return nil, badParam("status is 1 to 40 characters of plain text", "status")
	}
	key, ok, err := (&reads.Env{DB: e.DB, Home: e.Home}).LabelKey(path)
	if err != nil {
		return nil, err
	}
	if !ok {
		nf := rpc.NotFoundError(path + " is not a worktree an agent works in")
		nf.Data = map[string]any{"what": "worktree", "path": path}
		return nil, nf
	}
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if _, err := tx.Exec("INSERT INTO meta(name, value, updated) VALUES(?,?,strftime('%s','now')) ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated = excluded.updated",
			key, status); err != nil {
			return nil, err
		}
		return map[string]any{"accepted": true, "said": path + ": status is " + status}, nil
	})
}

// worktree.remove: an orphan worktree (worktrees.list orphan) goes, chosen by the founder. One merged into main with nothing changed goes
// at once and its branch with it (git branch -d); one that holds work is refused, gate dirty, saying what, unless force, which removes it
// and leaves its branch. A path that is no orphan is refused, gate orphan: an agent's worktree goes only with the agent.
func (e *Env) worktreeRemove(c *rpc.Conn, p map[string]any) (any, error) {
	path, _ := p["path"].(string)
	if path == "" {
		return nil, badParam("path is a worktree path", "path")
	}
	force, ok := p["force"].(bool)
	if _, has := p["force"]; has && !ok {
		return nil, badParam("force is true or false", "force")
	}
	wt, repo, found, err := worktreeprune.FindOrphan(e.DB, e.Home, path)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, refused(path+" is no orphan worktree: an agent works in it, or it is not a worktree of an agent's repo", "orphan")
	}
	merged, files, commits := worktreeprune.Unsaved(wt.Path)
	holds := !merged || files > 0 || commits > 0
	if holds && !force {
		r := rpc.Refused(fmt.Sprintf("%s holds work: %d files changed, %d commits not on main; force removes it", path, files, commits), "dirty", "")
		r.Data["files_changed"], r.Data["commits_ahead"] = files, commits
		return nil, r
	}
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		remove := worktreeprune.Remove
		if holds {
			remove = worktreeprune.RemoveForce
		}
		if ok, why := remove(wt.Path); !ok {
			return nil, refused(path+": git did not remove it: "+why, "git")
		}
		if err := db.ClearMeta(tx, worktreeprune.SizePrefix+worktreeprune.Real(path)); err != nil {
			return nil, err
		}
		deleted := !holds && worktreeprune.DeleteMergedBranch(repo, wt.Branch)
		return map[string]any{"accepted": true, "said": path + " removed", "branch_deleted": deleted}, nil
	})
}

// worktree.declare: the calling agent records a worktree of an agent's repository as one it works in, beside the one its session
// started in (agent_state.extra_worktrees), so no list calls it an orphan and it goes with the agent. One already recorded is said so.
func (e *Env) worktreeDeclare(c *rpc.Conn, p map[string]any) (any, error) {
	if c.Caller.Role != "agent" {
		return nil, rpc.Refused("worktree.declare is for an agent: it records a worktree the caller works in", "role", "")
	}
	path, _ := p["path"].(string)
	if path == "" {
		return nil, badParam("path is a worktree path", "path")
	}
	real := worktreeprune.Real(path)
	if !worktreeprune.InAgentRepo(e.Home, real) {
		nf := rpc.NotFoundError(path + " is not a worktree of an agent's repository")
		nf.Data = map[string]any{"what": "worktree", "path": path}
		return nil, nf
	}
	me := c.Caller.Actor()
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		have, err := worktreeprune.Of(tx, me)
		if err != nil {
			return nil, err
		}
		for _, h := range have {
			if h == real {
				return map[string]any{"accepted": true, "said": real + " is already one of " + me + "'s worktrees"}, nil
			}
		}
		row, err := db.GetAgentState(tx, me)
		if err != nil {
			return nil, err
		}
		var extras []string
		if row != nil && row.ExtraWorktrees.String != "" {
			if err := json.Unmarshal([]byte(row.ExtraWorktrees.String), &extras); err != nil {
				return nil, fmt.Errorf("%s's extra_worktrees: %w", me, err)
			}
		}
		if err := db.SetExtraWorktrees(tx, me, append(extras, real)); err != nil {
			return nil, err
		}
		return map[string]any{"accepted": true, "said": real + " is recorded as one of " + me + "'s worktrees"}, nil
	})
}
