package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

// A running Claude Code session leaves <config dir>/sessions/<pid>.json (its working directory, session id and peer protocol). A terminal
// agent's session is the one of those whose directory is the agent's worktree, whose process is there and whose terminal is still listed:
// the process table is not read, and a file left by a session that died is not taken for one that runs.

// terminalSessions is the terminal agents with a session running, agent to the session's pid.
func (p *Parts) terminalSessions() map[string]int {
	out := map[string]int{}
	if p.LC == nil {
		return out
	}
	live := p.LC.term.LiveTerminals()
	for _, agent := range paths.ListAgents(p.home) {
		if p.Kind(agent) {
			continue
		}
		row, err := db.GetAgentState(p.db, agent)
		if err != nil || row == nil || row.Terminal.String == "" || (live != nil && !live[row.Terminal.String]) {
			continue
		}
		wt := lifecycle.WorktreePath(row.Worktree.String)
		dir, err := team.AccountConfigDir(p.home, row.Account.String)
		if wt == "" || err != nil || row.Account.String == "" {
			continue
		}
		if pid := sessionIn(filepath.Join(dir, "sessions"), wt); pid > 0 {
			out[agent] = pid
		}
	}
	return out
}

// sessionIn is the pid of a running session whose working directory is wt, 0 for none.
func sessionIn(dir, wt string) int {
	files, _ := filepath.Glob(filepath.Join(dir, "*.json"))
	for _, f := range files {
		pid, err := strconv.Atoi(strings.TrimSuffix(filepath.Base(f), ".json"))
		if err != nil || pid <= 0 {
			continue
		}
		b, err := os.ReadFile(f)
		if err != nil {
			continue
		}
		var rec struct {
			Cwd string `json:"cwd"`
		}
		if json.Unmarshal(b, &rec) != nil || !sameDir(rec.Cwd, wt) {
			continue
		}
		if err := syscall.Kill(pid, 0); err == nil || err == syscall.EPERM {
			return pid
		}
	}
	return 0
}

func sameDir(a, b string) bool {
	if a == "" || b == "" {
		return false
	}
	ra, errA := filepath.EvalSymlinks(a)
	rb, errB := filepath.EvalSymlinks(b)
	if errA != nil || errB != nil {
		return filepath.Clean(a) == filepath.Clean(b)
	}
	return ra == rb
}
