package termchan

import (
	"encoding/base64"
	"strconv"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/rpc"
)

// terminal.input, the input holder's alone (handline/daemon/rpc_terminal.py); terminal.resize, the holder's while there is one and any
// attached connection's while there is none, down to the floor the screen reader needs; and the holder notice. The size before a holder's
// first resize is given back when they leave, and the size before the viewers' first when the last attach goes and nobody holds the input.

const (
	// MaxInput is the most bytes one terminal.input carries.
	MaxInput         = 16384
	maxCols, maxRows = 500, 200
	// The screen reader matches whole lines: narrower than this, Claude Code wraps a dialog's footer and options and the daemon no
	// longer recognises the dialog. Only a holder may go under it.
	unheldCols, unheldRows = 80, 24
	typingElsewhere        = "someone else is typing at this agent"
)

// HoldersChanged tells every client of agent's terminal who holds its input now, and when the daemon does again, puts back the size a
// holder's resize took. It runs wherever a hold is found to have changed, an idle expiry noticed by a gate check included.
func (e *Env) HoldersChanged(agent string, holders []map[string]any) {
	handle, err := e.terminalOf(agent)
	if err != nil || handle == "" {
		return
	}
	open := e.Hub.OpenAll(handle)
	for _, ch := range open {
		ch.Notify("holder", map[string]any{"holders": holders, "format": ch.Fmt})
	}
	e.Bytes.Notify(handle, "holder", map[string]any{"holders": holders})
	if holders[0]["kind"] != "daemon" {
		return
	}
	e.giveBack(e.restore, handle, open)
	e.lastLeft(handle)
}

// giveBack puts back the size owed[handle] keeps, if any.
func (e *Env) giveBack(owed map[string][2]int, handle string, open []*Channel) {
	e.mu.Lock()
	size, ok := owed[handle]
	delete(owed, handle)
	e.mu.Unlock()
	if !ok {
		return
	}
	if ok, _ := e.host().Resize(handle, size[0], size[1]); ok {
		e.resized(handle, open, size[0], size[1])
	}
}

// lastLeft gives back the size from before the viewers' first resize once no connection watches handle and nobody holds its input.
func (e *Env) lastLeft(handle string) {
	e.mu.Lock()
	for _, a := range e.conns {
		a.mu.Lock()
		for k := range a.m {
			if k[0] == handle {
				a.mu.Unlock()
				e.mu.Unlock()
				return
			}
		}
		a.mu.Unlock()
	}
	e.mu.Unlock()
	_, agent, err := e.handleOf(map[string]any{"terminal": handle})
	if err != nil || agent == "" || e.Holds == nil {
		return
	}
	if row, err := e.Holds.Holder(agent); err != nil || row != nil {
		return
	}
	e.giveBack(e.unheld, handle, e.Hub.OpenAll(handle))
}

// resized tells the watchers of handle its size changed: a text client a resize notice, a bytes client a snapshot.
func (e *Env) resized(handle string, open []*Channel, cols, rows int) {
	for _, ch := range open {
		ch.setSize(cols, rows)
		ch.Notify("resize", map[string]any{"cols": cols, "rows": rows})
	}
	e.Bytes.Resized(handle)
}

// holderOf is the holder gate: agent's holder row, refused when the caller is not it.
func (e *Env) holderOf(c *rpc.Conn, agent, what string) error {
	row, err := e.Holds.Holder(agent)
	if err != nil {
		return err
	}
	if row == nil || row.Name != callerName(c) {
		by := "; the daemon does"
		if row != nil {
			by = "; " + row.Name + " does"
		}
		return rpc.Refused("only whoever holds the terminal's input may "+what+by, "input_holder", "")
	}
	return nil
}

func callerName(c *rpc.Conn) string {
	n, _ := c.Caller.Info["name"].(string)
	return n
}

func (e *Env) input(c *rpc.Conn, params map[string]any) (any, error) {
	data, _ := params["data"].(string)
	raw, err := base64.StdEncoding.Strict().DecodeString(data)
	if _, isStr := params["data"].(string); !isStr || err != nil {
		return nil, bad("data is base64", "data")
	}
	if len(raw) < 1 || len(raw) > MaxInput {
		return nil, bad("data is 1 to 16384 bytes", "data")
	}
	handle, agent, err := e.handleOf(map[string]any{"terminal": params["terminal"]})
	if err != nil {
		return nil, err
	}
	if agent == "" {
		return nil, rpc.Refused("no agent has this terminal, so it has no input holder to take its keystrokes", "input_holder", "")
	}
	hold := lock.TakeTerminal(e.Home, agent)
	if hold == nil {
		return nil, rpc.Refused(agent+": "+typingElsewhere, "terminal_lock", "")
	}
	defer hold.Release()
	row, err := e.Holds.Holder(agent)
	if err != nil {
		return nil, err
	}
	switch {
	case row == nil:
		return nil, rpc.Refused(agent+"'s input is held by the daemon: take it first", "input_holder", "")
	case row.Name != callerName(c):
		return nil, rpc.Refused(inputhold.HeldWhy(agent, row.Name), "input_holder", "")
	}
	h := e.host()
	if !h.Backend.Available() || !h.ServesBytes() {
		return nil, rpc.Refused("the terminal host ("+h.Backend.Name()+") cannot send raw bytes", "unsupported", "")
	}
	if ok, why := h.SendBytes(handle, raw); !ok {
		return nil, rpc.Refused(agent+": nothing sent - "+why, "state", "")
	}
	if _, err := db.AddEvent(e.DB, agent, "type", db.KV{K: "actor", V: c.Caller.ActorInfo()}, db.KV{K: "bytes", V: len(raw)}); err != nil {
		return nil, err
	}
	if err := e.Holds.Touch(agent, callerName(c)); err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": " + strconv.Itoa(len(raw)) + " bytes sent"}, nil
}

func (e *Env) resize(c *rpc.Conn, params map[string]any) (any, error) {
	cols, err := intParam(params, "cols", 10, maxCols)
	if err != nil {
		return nil, err
	}
	rows, err := intParam(params, "rows", 3, maxRows)
	if err != nil {
		return nil, err
	}
	handle, agent, err := e.handleOf(map[string]any{"terminal": params["terminal"]})
	if err != nil {
		return nil, err
	}
	if agent == "" {
		return nil, rpc.Refused("no agent has this terminal, so it has no input holder to resize it", "input_holder", "")
	}
	holder, err := e.Holds.Holder(agent)
	if err != nil {
		return nil, err
	}
	if holder != nil {
		if err := e.holderOf(c, agent, "resize it"); err != nil {
			return nil, err
		}
	} else if !e.attached(c, handle) {
		return nil, rpc.Refused("attach "+agent+"'s terminal to resize it", "input_holder", "")
	} else if cols < unheldCols || rows < unheldRows {
		return nil, rpc.Refused("under "+strconv.Itoa(unheldCols)+" by "+strconv.Itoa(unheldRows)+" only whoever holds "+agent+
			"'s input may resize it: the daemon cannot read its dialogs narrower", "input_holder", "")
	}
	h := e.host()
	beforeCols, beforeRows, known := h.Size(handle)
	if ok, why := h.Resize(handle, cols, rows); !ok {
		return nil, rpc.Refused(agent+"'s terminal host cannot resize it: "+why, "unsupported", "")
	}
	if known {
		owed := e.restore
		if holder == nil {
			owed = e.unheld
		}
		e.mu.Lock()
		if _, kept := owed[handle]; !kept {
			owed[handle] = [2]int{beforeCols, beforeRows}
		}
		e.mu.Unlock()
	}
	e.resized(handle, e.Hub.OpenAll(handle), cols, rows)
	if holder == nil {
		return map[string]any{"accepted": true, "said": agent + ": terminal is " + strconv.Itoa(cols) + " by " + strconv.Itoa(rows)}, nil
	}
	if err := e.Holds.Touch(agent, callerName(c)); err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": terminal is " + strconv.Itoa(cols) + " by " + strconv.Itoa(rows)}, nil
}
