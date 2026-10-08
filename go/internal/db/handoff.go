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
