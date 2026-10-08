package session

import (
	"sort"

	"github.com/handline/handline/go/internal/db"
)

// The states an agent is in (daemon/states.py).
const (
	Running  = "RUNNING"
	Handoff  = "HANDOFF"
	Stalled  = "STALLED"
	Idle     = "IDLE"
	Queued   = "QUEUED"
	Parked   = "PARKED"
	Degraded = "DEGRADED"
)

// AgentRow is an agent_state row as these modules read it.
type AgentRow struct {
	Agent    string
	State    string
	Detail   string
	Terminal string
	ClosedAt int64 // 0 when open
}

func agentRow(s *db.AgentState) *AgentRow {
	if s == nil {
		return nil
	}
	return &AgentRow{s.Agent, s.State, s.Detail.String, s.Terminal.String, s.ClosedAt.Int64}
}

// GetAgentState is the agent's row, nil when it has none.
func (e *Env) GetAgentState(agent string) (*AgentRow, error) {
	s, err := db.GetAgentState(e.DB, agent)
	return agentRow(s), err
}

// AllAgentStates is every agent's row, by agent.
func (e *Env) AllAgentStates() ([]*AgentRow, error) {
	all, err := db.AllAgentStates(e.DB)
	out := make([]*AgentRow, len(all))
	for i, s := range all {
		out[i] = agentRow(s)
	}
	return out, err
}

// AddEvent appends an event stamped now, its payload's keys in sorted order.
func (e *Env) AddEvent(agent, kind string, payload map[string]any) error {
	keys := make([]string, 0, len(payload))
	for k := range payload {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	kv := make([]db.KV, len(keys))
	for i, k := range keys {
		kv[i] = db.KV{K: k, V: payload[k]}
	}
	_, err := db.AddEventAt(e.DB, e.now(), agent, kind, kv...)
	return err
}

// LastEventTS is when the agent last had an event of this kind, 0 when never.
func (e *Env) LastEventTS(agent, kind string) int64 {
	ts, _, _ := db.LastEventTS(e.DB, agent, kind)
	return ts
}

// LastActivityID is the row id of the agent's most recent event of one of kinds; ok is false when it has none.
func (e *Env) LastActivityID(agent string, kinds []string) (id int64, ok bool) {
	id, ok, _ = db.LastActivityID(e.DB, agent, kinds)
	return id, ok
}

// GetCursor is the named cursor, 0 when unset.
func (e *Env) GetCursor(name string) (int64, error) { return db.Cursor(e.DB, name) }

// SetCursor writes the named cursor.
func (e *Env) SetCursor(name string, id int64) error { return db.SetCursor(e.DB, name, id) }

// Block is a delivery_blocks row.
type Block struct {
	Agent, Kind, Detail, Work string
	Since, ShowAt, Seen       int64
	RequestID                 int64 // 0 when none
	Origin, Title             string
}

func block(b *db.Block) *Block {
	if b == nil {
		return nil
	}
	return &Block{b.Agent, b.Kind, b.Detail, b.Work, b.Since, b.ShowAt, b.Seen, b.RequestID.Int64, b.Origin, b.Title}
}

// BlockGet is the agent's block, nil when it has none.
func (e *Env) BlockGet(agent string) (*Block, error) {
	b, err := db.BlockGet(e.DB, agent)
	return block(b), err
}

// Blocks is every block, oldest first.
func (e *Env) Blocks() ([]*Block, error) {
	all, err := db.Blocks(e.DB)
	out := make([]*Block, len(all))
	for i, b := range all {
		out[i] = block(b)
	}
	return out, err
}

// BlockOpen records a new block for the agent, replacing one it had.
func (e *Env) BlockOpen(agent, kind, detail, work string, now, showAt int64, origin, title string) error {
	return db.BlockOpen(e.DB, agent, kind, detail, work, now, showAt, origin, title)
}

// BlockUpdate re-reads a standing block.
func (e *Env) BlockUpdate(agent, kind, detail, work string, now int64, title string) error {
	return db.BlockUpdate(e.DB, agent, kind, detail, work, now, title)
}

// BlockSetRequest records the request raised for these agents' blocks.
func (e *Env) BlockSetRequest(agents []string, rid int64) error {
	return db.BlockSetRequest(e.DB, agents, rid)
}

// BlockClear ends the agent's block.
func (e *Env) BlockClear(agent string) error { return db.BlockClear(e.DB, agent) }

// Press is a dialog_presses row.
type Press struct {
	At                   int64
	Title, Outcome, Note string
}

// PressAdd records one press at a dialog.
func (e *Env) PressAdd(agent string, at int64, title, outcome, note string) error {
	return db.PressAdd(e.DB, agent, at, title, outcome, note)
}

// PressesSince is the agent's presses at or after since, in the order they were made.
func (e *Env) PressesSince(agent string, since int64) ([]Press, error) {
	all, err := db.PressesSince(e.DB, agent, since)
	out := make([]Press, len(all))
	for i, p := range all {
		out[i] = Press{p.At, p.Title, p.Outcome, p.Note}
	}
	return out, err
}
