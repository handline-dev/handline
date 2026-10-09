package watcher

import "database/sql"

// ChatTouchedSQL reads the requests that changed or were answered since :last: a request that changed (state_at, else its own ts) and the
// request a say settles, each by its own index (requests_touched, requests_settles_ts), so a pass reads the rows since :last and no more.
// It is the chat and the inbox detectors' alike.
const ChatTouchedSQL = "SELECT id, MAX(t) AS touched FROM (SELECT id, COALESCE(state_at, ts) AS t FROM requests WHERE settles IS NULL AND COALESCE(state_at, ts) >= :last " +
	"UNION ALL SELECT settles, ts FROM requests WHERE settles IS NOT NULL AND ts >= :last) GROUP BY id ORDER BY touched, id"

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
