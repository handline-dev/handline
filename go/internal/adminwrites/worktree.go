package adminwrites

import (
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// worktree.set_status: a worktree's status on the terminal host, which keeps it (worktrees.list's `status` is git's). This daemon reaches no
// host that keeps one, so after the checks it refuses, as the Python daemon does on a host that keeps none.

const statusMax = 40

// worktreePath is the directory an agent works in, out of what its row holds: a selector (repo id, `::`, path) or a plain path.
func worktreePath(recorded string) string {
	if recorded == "" {
		return ""
	}
	path := recorded
	if i := strings.LastIndex(recorded, "::"); i >= 0 {
		path = recorded[i+2:]
	}
	if st, err := os.Stat(path); err == nil && st.IsDir() {
		return path
	}
	return ""
}

func (e *Env) worktreeSetStatus(c *rpc.Conn, p map[string]any) (any, error) {
	status, ok := p["status"].(string)
	status = strings.TrimSpace(status)
	if !ok || status == "" || textsafe.HasUnsafe(status) || len([]rune(status)) > statusMax {
		return nil, badParam("status is one plain line of at most 40 characters", "status")
	}
	path, ok := p["path"].(string)
	if !ok || path == "" {
		return nil, badParam("path is a worktree's path", "path")
	}
	real := func(s string) string {
		if r, err := filepath.EvalSymlinks(s); err == nil {
			return r
		}
		return s
	}
	found := false
	for _, name := range paths.ListAgents(e.Home) {
		row, err := db.GetAgentState(e.DB, name)
		if err != nil {
			return nil, err
		}
		if row == nil {
			continue
		}
		if wt := worktreePath(row.Worktree.String); wt != "" && real(wt) == real(path) {
			found = true
			break
		}
	}
	if !found {
		return nil, notFound("worktree", path, "no worktree "+path)
	}
	return nil, refused("no terminal host this daemon reaches keeps a worktree status", "state")
}
