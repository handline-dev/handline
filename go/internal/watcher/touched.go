package watcher

import "database/sql"

// ChatTouchedSQL reads the requests that changed since :last (state_at, else their own ts), by requests_touched_at, so a pass reads the
// rows since :last and no more. It is the chat and the inbox detectors' alike.
const ChatTouchedSQL = "SELECT id, COALESCE(state_at, ts) AS touched FROM requests WHERE COALESCE(state_at, ts) >= :last ORDER BY touched, id"

type touched struct {
	id   int64
	when sql.NullInt64
}

// requestsTouched runs query with its named parameters and reads the (id, touched) rows.
func requestsTouched(q *sql.DB, query string, args ...any) ([]touched, error) {
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []touched
	for rows.Next() {
		var t touched
		if err := rows.Scan(&t.id, &t.when); err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}
