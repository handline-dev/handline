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
