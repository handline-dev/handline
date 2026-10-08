package session

import (
	"database/sql"
	"encoding/json"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
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

const agentCols = "agent, state, detail, terminal, closed_at"

func scanAgent(r interface{ Scan(...any) error }) (*AgentRow, error) {
	var a AgentRow
	var detail, term sql.NullString
	var closed sql.NullInt64
	if err := r.Scan(&a.Agent, &a.State, &detail, &term, &closed); err != nil {
		return nil, err
	}
	a.Detail, a.Terminal, a.ClosedAt = detail.String, term.String, closed.Int64
	return &a, nil
}

// GetAgentState is the agent's row, nil when it has none.
func (e *Env) GetAgentState(agent string) (*AgentRow, error) {
	a, err := scanAgent(e.DB.QueryRow("SELECT "+agentCols+" FROM agent_state WHERE agent = ?", agent))
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return a, err
}

// AllAgentStates is every agent's row, by agent.
func (e *Env) AllAgentStates() ([]*AgentRow, error) {
	rows, err := e.DB.Query("SELECT " + agentCols + " FROM agent_state ORDER BY agent")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*AgentRow
	for rows.Next() {
		a, err := scanAgent(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// AddEvent appends an event stamped now.
func (e *Env) AddEvent(agent, kind string, payload map[string]any) error {
	var p any
	if len(payload) > 0 {
		p = rpc.PyJSON(payload, false, false)
	}
	_, err := e.DB.Exec("INSERT INTO events(ts, agent, account, kind, payload, dedup_key) VALUES(?,?,?,?,?,?) ON CONFLICT(dedup_key) DO NOTHING",
		e.now(), agent, nil, kind, p, nil)
	return err
}

// LastEventTS is when the agent last had an event of this kind, 0 when never.
func (e *Env) LastEventTS(agent, kind string) int64 {
	var t sql.NullInt64
	_ = e.DB.QueryRow("SELECT MAX(ts) FROM events WHERE agent = ? AND kind = ?", agent, kind).Scan(&t)
	return t.Int64
}

// LastActivityID is the row id of the agent's most recent event of one of kinds; ok is false when it has none.
func (e *Env) LastActivityID(agent string, kinds []string) (id int64, ok bool) {
	args := []any{agent}
	for _, k := range kinds {
		args = append(args, k)
	}
	var i sql.NullInt64
	_ = e.DB.QueryRow("SELECT MAX(id) FROM events WHERE agent = ? AND kind IN ("+strings.TrimSuffix(strings.Repeat("?,", len(kinds)), ",")+")", args...).Scan(&i)
	return i.Int64, i.Valid
}

// GetCursor is the named cursor, 0 when unset.
func (e *Env) GetCursor(name string) int64 {
	var v int64
	_ = e.DB.QueryRow("SELECT row_id FROM cursors WHERE name = ?", name).Scan(&v)
	return v
}

// SetCursor writes the named cursor.
func (e *Env) SetCursor(name string, id int64) error {
	_, err := e.DB.Exec("INSERT INTO cursors(name, row_id, updated) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET row_id=excluded.row_id, updated=excluded.updated",
		name, id, e.now())
	return err
}

// Block is a delivery_blocks row.
type Block struct {
	Agent, Kind, Detail, Work string
	Since, ShowAt, Seen       int64
	RequestID                 int64 // 0 when none
	Origin, Title             string
}

const blockCols = "agent, kind, detail, work, since, show_at, seen, request_id, origin, title"

func scanBlock(r interface{ Scan(...any) error }) (*Block, error) {
	var b Block
	var rid sql.NullInt64
	var title sql.NullString
	if err := r.Scan(&b.Agent, &b.Kind, &b.Detail, &b.Work, &b.Since, &b.ShowAt, &b.Seen, &rid, &b.Origin, &title); err != nil {
		return nil, err
	}
	b.RequestID, b.Title = rid.Int64, title.String
	return &b, nil
}

// BlockGet is the agent's block, nil when it has none.
func (e *Env) BlockGet(agent string) (*Block, error) {
	b, err := scanBlock(e.DB.QueryRow("SELECT "+blockCols+" FROM delivery_blocks WHERE agent = ?", agent))
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return b, err
}

// Blocks is every block, oldest first.
func (e *Env) Blocks() ([]*Block, error) {
	rows, err := e.DB.Query("SELECT " + blockCols + " FROM delivery_blocks ORDER BY since, agent")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Block
	for rows.Next() {
		b, err := scanBlock(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, rows.Err()
}

// BlockOpen records a new block for the agent, replacing one it had.
func (e *Env) BlockOpen(agent, kind, detail, work string, now, showAt int64, origin, title string) error {
	_, err := e.DB.Exec("INSERT OR REPLACE INTO delivery_blocks(agent, kind, detail, work, since, show_at, seen, request_id, origin, title) VALUES(?,?,?,?,?,?,?,NULL,?,?)",
		agent, kind, detail, work, now, showAt, now, origin, title)
	return err
}

// BlockUpdate re-reads a standing block.
func (e *Env) BlockUpdate(agent, kind, detail, work string, now int64, title string) error {
	_, err := e.DB.Exec("UPDATE delivery_blocks SET kind = ?, detail = ?, work = ?, seen = ?, title = ? WHERE agent = ?",
		kind, detail, work, now, title, agent)
	return err
}

// BlockSetRequest records the request raised for these agents' blocks.
func (e *Env) BlockSetRequest(agents []string, rid int64) error {
	for _, a := range agents {
		if _, err := e.DB.Exec("UPDATE delivery_blocks SET request_id = ? WHERE agent = ?", rid, a); err != nil {
			return err
		}
	}
	return nil
}

// BlockClear ends the agent's block.
func (e *Env) BlockClear(agent string) error {
	_, err := e.DB.Exec("DELETE FROM delivery_blocks WHERE agent = ?", agent)
	return err
}

// Press is a dialog_presses row.
type Press struct {
	At                   int64
	Title, Outcome, Note string
}

// PressAdd records one press at a dialog.
func (e *Env) PressAdd(agent string, at int64, title, outcome, note string) error {
	_, err := e.DB.Exec("INSERT INTO dialog_presses(agent, at, title, outcome, note) VALUES(?,?,?,?,?)", agent, at, title, outcome, note)
	return err
}

// PressesSince is the agent's presses at or after since, in the order they were made.
func (e *Env) PressesSince(agent string, since int64) ([]Press, error) {
	rows, err := e.DB.Query("SELECT at, title, outcome, note FROM dialog_presses WHERE agent = ? AND at >= ? ORDER BY id", agent, since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Press
	for rows.Next() {
		var p Press
		if err := rows.Scan(&p.At, &p.Title, &p.Outcome, &p.Note); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

func unjson(s string) map[string]any {
	var m map[string]any
	if json.Unmarshal([]byte(s), &m) != nil {
		return nil
	}
	return m
}
