package db

import "database/sql"

// Pending is one pending_messages row: text the daemon still owes an agent's terminal.
type Pending struct {
	ID, TS int64
	Agent  string
	Text   string
}

// AddPending is db.add_pending.
func AddPending(q Q, agent, text string, ts int64) (int64, error) {
	res, err := q.Exec("INSERT INTO pending_messages(agent, text, ts) VALUES(?,?,?)", agent, text, ts)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// GetPending is db.get_pending: nil once it has been typed and deleted.
func GetPending(q Q, id int64) (*Pending, error) {
	p := &Pending{}
	err := q.QueryRow("SELECT id, agent, text, ts FROM pending_messages WHERE id = ?", id).Scan(&p.ID, &p.Agent, &p.Text, &p.TS)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return p, err
}

// ListPending is db.list_pending, oldest first: one agent's, or everyone's when agent is "".
func ListPending(q Q, agent string) ([]*Pending, error) {
	query, args := "SELECT id, agent, text, ts FROM pending_messages ORDER BY id", []any(nil)
	if agent != "" {
		query, args = "SELECT id, agent, text, ts FROM pending_messages WHERE agent = ? ORDER BY id", []any{agent}
	}
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Pending
	for rows.Next() {
		p := &Pending{}
		if err := rows.Scan(&p.ID, &p.Agent, &p.Text, &p.TS); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// DeletePending is db.delete_pending.
func DeletePending(q Q, id int64) error {
	_, err := q.Exec("DELETE FROM pending_messages WHERE id = ?", id)
	return err
}
