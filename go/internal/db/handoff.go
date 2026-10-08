package db

import "database/sql"

// GetHandoff is db.get_handoff: the record, "" when there is none.
func GetHandoff(q Q, agent string) (string, error) {
	var h sql.NullString
	err := q.QueryRow("SELECT handoff FROM agent_state WHERE agent = ?", agent).Scan(&h)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return h.String, err
}

// SetHandoff is db.set_handoff: keep the record. Keeping one for an agent with no row creates it idle.
func SetHandoff(q Q, agent, text string) error {
	_, err := q.Exec("INSERT INTO agent_state(agent, state, updated, handoff) VALUES(?, 'IDLE', ?, ?) ON CONFLICT(agent) DO UPDATE SET handoff = excluded.handoff", agent, Now(), text)
	return err
}

// ClearHandoff is db.set_handoff with None.
func ClearHandoff(q Q, agent string) error {
	_, err := q.Exec("UPDATE agent_state SET handoff = NULL WHERE agent = ?", agent)
	return err
}

// LastHandoffTrigger is the trigger_kind of the agent's most recently updated handoff (db.last_handoff), "" when it has none.
func LastHandoffTrigger(q Q, agent string) (string, error) {
	var k sql.NullString
	err := q.QueryRow("SELECT trigger_kind FROM handoff WHERE agent = ? ORDER BY updated DESC LIMIT 1", agent).Scan(&k)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return k.String, err
}

// HandoffRow is one handoff row.
type HandoffRow struct {
	ID, Agent, Step, State string
	SessionID, TriggerKind sql.NullString
	Deadline               sql.NullInt64
	Detail                 sql.NullString
	Updated                int64
}

// InFlightHandoffs is db.in_flight_handoffs: the handoffs not yet finished, oldest update first.
func InFlightHandoffs(q Q) ([]HandoffRow, error) {
	rows, err := q.Query("SELECT id, agent, session_id, trigger_kind, step, deadline, state, detail, updated FROM handoff WHERE state = 'in_flight' ORDER BY updated")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []HandoffRow
	for rows.Next() {
		var h HandoffRow
		if err := rows.Scan(&h.ID, &h.Agent, &h.SessionID, &h.TriggerKind, &h.Step, &h.Deadline, &h.State, &h.Detail, &h.Updated); err != nil {
			return nil, err
		}
		out = append(out, h)
	}
	return out, rows.Err()
}

// AdvanceHandoff is db.advance_handoff: commit one step; an empty state or detail keeps the one there.
func AdvanceHandoff(q Q, id, step, state, detail string) error {
	_, err := q.Exec("UPDATE handoff SET step = ?, state = COALESCE(?, state), detail = COALESCE(?, detail), updated = ? WHERE id = ?",
		step, nullStr(state), nullStr(detail), Now(), id)
	return err
}

// StartHandoff is db.start_handoff: an in-flight handoff at its first step.
func StartHandoff(q Q, id, agent, sessionID, triggerKind string, deadline int64) error {
	_, err := q.Exec("INSERT OR REPLACE INTO handoff(id, agent, session_id, trigger_kind, step, deadline, state, detail, updated) VALUES(?,?,?,?,'requested',?,'in_flight',NULL,?)",
		id, agent, nullStr(sessionID), triggerKind, deadline, Now())
	return err
}

// InFlightFor is db.in_flight_for: the agent's oldest handoff still in flight, nil when none.
func InFlightFor(q Q, agent string) (*HandoffRow, error) {
	var h HandoffRow
	err := q.QueryRow("SELECT id, agent, session_id, trigger_kind, step, deadline, state, detail, updated FROM handoff WHERE agent = ? AND state = 'in_flight' ORDER BY updated LIMIT 1", agent).
		Scan(&h.ID, &h.Agent, &h.SessionID, &h.TriggerKind, &h.Step, &h.Deadline, &h.State, &h.Detail, &h.Updated)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return &h, err
}

// Session starts kept per agent, and the window in which a fresh session's first hook claims one.
const (
	SessionTextMaxChars = 20000
	SessionStartsKept   = 50
	BindWindowS         = 600
)

func clip(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// NoteSessionStart is db.note_session_start: keep that a session was started, why and what it was given; only the oldest beyond
// SessionStartsKept go. A fresh launch has no session id yet (BindSessionStart).
func NoteSessionStart(q Q, agent, cause string, resumed bool, sessionID, text, prompt, actor string) (int64, error) {
	r := 0
	if resumed {
		r = 1
	}
	res, err := q.Exec("INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt, actor) VALUES(?,?,?,?,?,?,?,?)",
		agent, Now(), nullStr(sessionID), cause, r, clip(text, SessionTextMaxChars), clip(prompt, SessionTextMaxChars), clip(actor, 80))
	if err != nil {
		return 0, err
	}
	id, _ := res.LastInsertId()
	_, err = q.Exec("DELETE FROM session_starts WHERE agent = ? AND id NOT IN (SELECT id FROM session_starts WHERE agent = ? ORDER BY id DESC LIMIT ?)", agent, agent, SessionStartsKept)
	return id, err
}
