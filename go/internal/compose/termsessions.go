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

// sessionRec is what a session file says: the process, the session, and what the session reports of itself (idle, busy, waiting with
// what it waits for, or at a shell).
type sessionRec struct {
	Pid        int
	SessionID  string
	Status     string
	WaitingFor string
}

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
		if rec, _, ok := p.terminalSession(agent, live); ok {
			out[agent] = rec.Pid
		}
	}
	return out
}

// terminalSession is the running session of a terminal agent and the account whose config directory holds its file (the account it
// spends, whatever agent.yaml says). live is the terminals the host lists, nil when it could not say.
func (p *Parts) terminalSession(agent string, live map[string]bool) (sessionRec, string, bool) {
	row, err := db.GetAgentState(p.db, agent)
	if err != nil || row == nil || row.Terminal.String == "" || (live != nil && !live[row.Terminal.String]) {
		return sessionRec{}, "", false
	}
	wt := lifecycle.WorktreePath(row.Worktree.String)
	if wt == "" {
		return sessionRec{}, "", false
	}
	accts := paths.ListAccounts(p.home)
	if row.Account.String != "" { // the recorded account first: a session found there is the expected one
		accts = append([]string{row.Account.String}, accts...)
	}
	for _, acct := range accts {
		dir, err := team.AccountConfigDir(p.home, acct)
		if err != nil {
			continue
		}
		if rec, ok := sessionIn(filepath.Join(dir, "sessions"), wt); ok {
			return rec, acct, true
		}
	}
	return sessionRec{}, "", false
}

// sessionIn is the running session whose working directory is wt.
func sessionIn(dir, wt string) (sessionRec, bool) {
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
			Cwd        string `json:"cwd"`
			SessionID  string `json:"sessionId"`
			Status     string `json:"status"`
			WaitingFor string `json:"waitingFor"`
		}
		if json.Unmarshal(b, &rec) != nil || !sameDir(rec.Cwd, wt) {
			continue
		}
		if err := syscall.Kill(pid, 0); err == nil || err == syscall.EPERM {
			return sessionRec{Pid: pid, SessionID: rec.SessionID, Status: rec.Status, WaitingFor: rec.WaitingFor}, true
		}
	}
	return sessionRec{}, false
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
