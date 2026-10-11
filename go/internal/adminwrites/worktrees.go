package adminwrites

import (
	"database/sql"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
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
