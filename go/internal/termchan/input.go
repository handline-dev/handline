package termchan

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
)

// input.take and input.release: who types at an agent's terminal (handline/daemon/rpc_input.py). A person or device takes it to
// type or answer without the daemon typing beside them, and gives it back, or loses it by being idle (inputhold). Taking and
// releasing record an input_holder event and tell whoever watches the terminal.

func (e *Env) inputAgent(params map[string]any) (string, error) {
	name, _ := params["agent"].(string)
	if name == "" {
		return "", bad("agent is a name", "agent")
	}
	if !has(paths.ListAgents(e.Home), name) {
		return "", notFound("no such agent: "+name, "agent", name)
	}
	if e.Structured != nil && e.Structured(name) {
		return "", rpc.Refused(name+" runs as a structured session: it has no terminal to type at", "unsupported", "")
	}
	return name, nil
}

// holderGate refuses while a person or device other than the caller holds agent's input; the daemon holding it is not a refusal.
func (e *Env) holderGate(c *rpc.Conn, agent string) error {
	r, err := e.holder(agent)
	if err != nil {
		return err
	}
	if me, _ := c.Caller.Info["name"].(string); r != nil && r.Name != me {
		return rpc.Refused(inputhold.HeldWhy(agent, r.Name), "input_holder", "")
	}
	return nil
}

// holder is whoever holds agent's input. An idle hold is lost here, and the loss is recorded and told like a take.
func (e *Env) holder(agent string) (*inputhold.Row, error) {
	r, dropped, err := inputhold.Holder(e.DB, e.Home, agent, e.now())
	if err == nil && dropped {
		err = e.HoldersChanged(agent)
	}
	return r, err
}

// HoldersChanged records that who holds agent's input changed and sends `holder` to every client of agent's terminal: a take, a
// release, or a hold found idle by whichever call read it (inputhold.Holder says so).
func (e *Env) HoldersChanged(agent string) error {
	holders, err := e.holders(agent)
	if err != nil {
		return err
	}
	list := make([]any, len(holders)) // the keys in the order Python's dict keeps them
	for i, h := range holders {
		list[i] = rpc.Pairs{{K: "kind", V: h["kind"]}, {K: "name", V: h["name"]}, {K: "since", V: h["since"]}}
	}
	if _, err := db.AddEvent(e.DB, agent, "input_holder", db.KV{K: "holders", V: list}); err != nil {
		return err
	}
	handle, err := e.terminalOf(agent)
	if err != nil || handle == "" {
		return nil
	}
	for _, ch := range e.Hub.OpenAll(handle) {
		ch.Notify("holder", map[string]any{"holders": holders, "format": ch.Fmt})
	}
	e.Bytes.Notify(handle, "holder", map[string]any{"holders": holders})
	return nil
}

func (e *Env) take(c *rpc.Conn, params map[string]any) (any, error) {
	agent, err := e.inputAgent(params)
	if err != nil {
		return nil, err
	}
	if err := e.holderGate(c, agent); err != nil {
		return nil, err
	}
	kind := "person"
	if c.Caller.Info["kind"] == "device" {
		kind = "device"
	}
	me, _ := c.Caller.Info["name"].(string)
	took, err := inputhold.Take(e.DB, agent, kind, me, e.now())
	if err != nil {
		return nil, err
	}
	if !took { // another took it first
		if err := e.holderGate(c, agent); err != nil {
			return nil, err
		}
		return nil, rpc.Refused(agent+"'s terminal was taken by someone else", "input_holder", "")
	}
	if err := e.HoldersChanged(agent); err != nil {
		return nil, err
	}
	holders, err := e.holders(agent)
	if err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": you hold its input", "holder": holders[0]}, nil
}

func (e *Env) release(c *rpc.Conn, params map[string]any) (any, error) {
	agent, err := e.inputAgent(params)
	if err != nil {
		return nil, err
	}
	r, err := e.holder(agent)
	if err != nil {
		return nil, err
	}
	if r == nil {
		return map[string]any{"accepted": true, "said": agent + ": the daemon already holds its input"}, nil
	}
	if err := e.holderGate(c, agent); err != nil { // only its holder gives it back
		return nil, err
	}
	if err := inputhold.Release(e.DB, agent); err != nil {
		return nil, err
	}
	if err := e.HoldersChanged(agent); err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": its input is the daemon's again"}, nil
}
