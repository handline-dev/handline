// Package debugterm is debug.terminals: every pane of the daemon's terminal server with what the daemon knows of it, for the hidden
// debug window that finds panes left open past their prompt cache and shells left behind after an agent's session exited.
//
// A pane is described from three places: the host (tmux's list of panes), the process table (whether an agent's CLI still runs under
// the pane) and the database (which agent the pane is, by agent_state, or was, by agent_terminals). The agent's view is read for its
// cache_until field alone; nothing else of it leaves.
package debugterm

import (
	"database/sql"
	"encoding/json"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// Proc is one row of the process table.
type Proc struct {
	PPID int
	Comm string
}

// Env is what debug.terminals reads.
type Env struct {
	DB      *sql.DB
	Backend func() term.Backend
	// Procs is the process table by pid; nil when it could not be read.
	Procs func() map[int]Proc
	Now   func() time.Time
}

// Register adds debug.terminals.
func Register(s *rpc.Server, e *Env) {
	s.Register("debug.terminals", rpc.Method{Role: "member", Handler: func(_ *rpc.Conn, _ map[string]any) (any, error) { return e.List() }})
}

// agentCLIs are the programs an agent session runs as. Claude Code also renames its process to its version (2.1.0), which tmux then
// reports as the pane's command.
var (
	agentCLIs = map[string]bool{"claude": true, "codex": true, "opencode": true}
	versionRE = regexp.MustCompile(`^\d+\.\d+\.\d+$`)
	shells    = map[string]bool{"zsh": true, "bash": true, "sh": true, "fish": true, "dash": true, "ksh": true, "tcsh": true, "csh": true}
)

func base(comm string) string { return strings.TrimPrefix(filepath.Base(strings.TrimSpace(comm)), "-") }

func isAgentCLI(comm string) bool { b := base(comm); return agentCLIs[b] || versionRE.MatchString(b) }

// agentUnder is the first agent CLI process at or under root, breadth first; 0 for none.
func agentUnder(procs map[int]Proc, root int) (int, string) {
	kids := map[int][]int{}
	for pid, p := range procs {
		kids[p.PPID] = append(kids[p.PPID], pid)
	}
	queue, seen := []int{root}, map[int]bool{}
	for len(queue) > 0 {
		pid := queue[0]
		queue = queue[1:]
		if seen[pid] || len(seen) > 4096 {
			continue
		}
		seen[pid] = true
		if p, ok := procs[pid]; ok && isAgentCLI(p.Comm) {
			return pid, base(p.Comm)
		}
		queue = append(queue, kids[pid]...)
	}
	return 0, ""
}

// List is the reply of debug.terminals.
func (e *Env) List() (any, error) {
	b := e.Backend()
	lister, ok := b.(term.PaneLister)
	if !ok || !b.Available() {
		return nil, rpc.Refused("the terminal host ("+b.Name()+") cannot list its panes", "unsupported", "")
	}
	panes, ok := lister.ListPanes()
	if !ok {
		return nil, rpc.Refused("the terminal host could not be reached", "state", "")
	}
	var procs map[int]Proc
	if e.Procs != nil {
		procs = e.Procs()
	}
	now := e.Now().Unix()
	out := make([]any, 0, len(panes))
	for _, p := range panes {
		row, err := e.pane(p, procs, now)
		if err != nil {
			return nil, err
		}
		out = append(out, row)
	}
	return map[string]any{"host": b.Name(), "now": stamp(now), "panes": out}, nil
}

func stamp(epoch int64) string { return time.Unix(epoch, 0).UTC().Format("2006-01-02T15:04:05Z") }

func orNil(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func (e *Env) pane(p term.Pane, procs map[int]Proc, now int64) (map[string]any, error) {
	m := map[string]any{
		"session": p.Session, "window": p.Window, "window_name": p.WindowName, "terminal": p.Handle, "command": p.Command,
		"path": orNil(p.Path), "dead": p.Dead, "pid": nil, "last_activity": nil, "idle_s": nil, "agent_process": nil,
		"agent": nil, "closed": false, "recorded_agent": nil, "state": nil, "account": nil, "session_id": nil, "worktree": nil, "cache_until": nil,
		// The daemon keeps no link from a tmux pane to an Orca terminal: an Orca-hosted agent has no tmux pane to list.
		"orca_terminal": nil,
	}
	if p.PID > 0 {
		m["pid"] = p.PID
	}
	if p.Activity > 0 {
		m["last_activity"], m["idle_s"] = stamp(p.Activity), max(now-p.Activity, 0)
	}
	process := "other"
	switch {
	case procs == nil && isAgentCLI(p.Command):
		process = "agent"
	case procs == nil:
		process = "unknown"
		if shells[base(p.Command)] {
			process = "shell"
		}
	default:
		if pid, name := agentUnder(procs, p.PID); pid != 0 {
			process, m["agent_process"] = "agent", map[string]any{"pid": pid, "name": name}
		} else if shells[base(p.Command)] {
			process = "shell"
		}
	}
	m["process"] = process
	agent, err := e.agentOf(p.Handle, m)
	if err != nil {
		return nil, err
	}
	m["orphan"] = agent == ""
	if agent == "" {
		var was sql.NullString
		err := e.DB.QueryRow("SELECT agent FROM agent_terminals WHERE handle = ? AND agent IS NOT NULL", p.Handle).Scan(&was)
		if err != nil && err != sql.ErrNoRows {
			return nil, err
		}
		m["recorded_agent"] = orNil(was.String)
	}
	return m, nil
}

// agentOf fills in the agent whose current terminal the pane is, and returns its name ("" for none).
func (e *Env) agentOf(handle string, m map[string]any) (string, error) {
	var agent, state, account, worktree, resume sql.NullString
	var closed sql.NullInt64
	err := e.DB.QueryRow("SELECT agent, state, account, worktree, resume_session, closed_at FROM agent_state WHERE terminal = ? ORDER BY updated DESC LIMIT 1",
		handle).Scan(&agent, &state, &account, &worktree, &resume, &closed)
	if err == sql.ErrNoRows {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	m["agent"], m["state"], m["account"] = agent.String, orNil(state.String), orNil(account.String)
	m["closed"] = closed.Valid && closed.Int64 != 0 // a closed agent keeps the handle of the pane it had
	if wt := worktree.String; wt != "" {
		if i := strings.Index(wt, "::"); i >= 0 { // `hl launch` records a selector: the repo's id, `::`, then the path
			wt = wt[i+2:]
		}
		m["worktree"] = wt
	}
	var sid sql.NullString
	err = e.DB.QueryRow("SELECT session_id FROM session_starts WHERE agent = ? AND session_id IS NOT NULL AND session_id != '' ORDER BY id DESC LIMIT 1",
		agent.String).Scan(&sid)
	if err != nil && err != sql.ErrNoRows {
		return "", err
	}
	if sid.String == "" {
		sid = resume
	}
	m["session_id"] = orNil(sid.String)
	var view sql.NullString
	if err := e.DB.QueryRow("SELECT view FROM agent_view WHERE agent = ?", agent.String).Scan(&view); err != nil && err != sql.ErrNoRows {
		return "", err
	}
	var v struct {
		CacheUntil *string `json:"cache_until"`
	}
	if view.String != "" && json.Unmarshal([]byte(view.String), &v) == nil && v.CacheUntil != nil {
		m["cache_until"] = *v.CacheUntil
	}
	return agent.String, nil
}

// ReadProcs is the machine's process table from ps; nil when ps fails.
func ReadProcs() map[int]Proc {
	table, err := exec.Command("ps", "-axo", "pid=,ppid=,comm=").Output()
	if err != nil {
		return nil
	}
	return ParseProcs(string(table))
}

// ParseProcs reads `ps -o pid=,ppid=,comm=`; a command may hold spaces.
func ParseProcs(table string) map[int]Proc {
	out := map[int]Proc{}
	for _, line := range strings.Split(table, "\n") {
		f := strings.Fields(line)
		if len(f) < 3 {
			continue
		}
		pid, e1 := strconv.Atoi(f[0])
		ppid, e2 := strconv.Atoi(f[1])
		if e1 == nil && e2 == nil {
			out[pid] = Proc{PPID: ppid, Comm: strings.Join(f[2:], " ")}
		}
	}
	return out
}
