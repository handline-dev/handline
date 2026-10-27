package termchan

import (
	"database/sql"
	"strconv"

	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// shell.open starts a plain login shell in an agent's worktree as a window of the terminal host, and shell.close ends it. A shell is
// watched, typed at and resized through the terminal methods like any pane, but it is the person's: it has no agent, so no input holder
// and no terminal lock stand between them and it, and no size floor (the daemon never reads its screen).

// MaxShells is the most shells open beside one agent.
const MaxShells = 8

func (e *Env) shells() (term.Shells, bool) {
	b := e.Backend()
	if !b.Available() {
		return nil, false
	}
	s, ok := b.(term.Shells)
	return s, ok
}

// shellOf is the agent a shell pane was opened beside, "" for any other pane.
func (e *Env) shellOf(handle string) string {
	if s, ok := e.shells(); ok {
		a, _ := s.PaneShell(handle)
		return a
	}
	return ""
}

func (e *Env) shellOpen(_ *rpc.Conn, params map[string]any) (any, error) {
	name, _ := params["agent"].(string)
	if name == "" {
		return nil, bad("agent is a name", "agent")
	}
	if !has(paths.ListAgents(e.Home), name) {
		return nil, notFound("no such agent: "+name, "agent", name)
	}
	var recorded sql.NullString
	if err := e.DB.QueryRow("SELECT worktree FROM agent_state WHERE agent = ?", name).Scan(&recorded); err != nil && err != sql.ErrNoRows {
		return nil, err
	}
	cwd := lifecycle.WorktreePath(recorded.String)
	if cwd == "" {
		return nil, notFound(name+" has no worktree to open a shell in", "worktree", name)
	}
	host, ok := e.shells()
	if !ok {
		return nil, rpc.Refused("the terminal host ("+e.Backend().Name()+") cannot open a shell", "unsupported", "")
	}
	open := 0
	for h := range e.Backend().ListTerminals() {
		if a, _ := host.PaneShell(h); a == name {
			open++
		}
	}
	if open >= MaxShells {
		return nil, rpc.Refused(name+" has "+strconv.Itoa(open)+" shells open; close one first", "state", "")
	}
	handle, why := host.CreateShell(cwd, name+"-sh", name)
	if handle == "" {
		return nil, rpc.Refused("no shell was opened: "+why, "state", "")
	}
	return map[string]any{"agent": name, "terminal": handle, "cwd": cwd, "host": e.Backend().Name()}, nil
}

func (e *Env) shellClose(_ *rpc.Conn, params map[string]any) (any, error) {
	handle, _ := params["terminal"].(string)
	if handle == "" {
		return nil, bad("terminal is a handle", "terminal")
	}
	agent := e.shellOf(handle)
	if agent == "" {
		return nil, notFound("no shell "+handle, "terminal", handle)
	}
	h, ok := e.Backend().(term.Host)
	if !ok {
		return nil, rpc.Refused("the terminal host cannot close a shell", "unsupported", "")
	}
	if ok, why := h.CloseTerminal(handle, true); !ok {
		return nil, rpc.Refused("shell "+handle+" was not closed: "+why, "state", "")
	}
	return map[string]any{"accepted": true, "said": agent + ": shell " + handle + " closed"}, nil
}

// shellInput types raw bytes at a shell.
func (e *Env) shellInput(handle string, raw []byte) (any, error) {
	h := e.host()
	if !h.Backend.Available() || !h.ServesBytes() {
		return nil, rpc.Refused("the terminal host ("+h.Backend.Name()+") cannot send raw bytes", "unsupported", "")
	}
	if ok, why := h.SendBytes(handle, raw); !ok {
		return nil, rpc.Refused("nothing sent - "+why, "state", "")
	}
	return map[string]any{"accepted": true, "said": strconv.Itoa(len(raw)) + " bytes sent"}, nil
}

// shellResize sizes a shell to what its viewer draws.
func (e *Env) shellResize(handle string, cols, rows int) (any, error) {
	h := e.host()
	if ok, why := h.Resize(handle, cols, rows); !ok {
		return nil, rpc.Refused("the terminal host cannot resize it: "+why, "unsupported", "")
	}
	e.resized(handle, e.Hub.OpenAll(handle), cols, rows)
	return map[string]any{"accepted": true, "said": "terminal is " + strconv.Itoa(cols) + " by " + strconv.Itoa(rows)}, nil
}
