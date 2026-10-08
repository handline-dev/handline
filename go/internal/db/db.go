// Package db is the supervisor's statements on the tables the Python daemon keeps (handline/db): the same SQL, on a
// database or a transaction. The tables are shared with Python until it is gone, so no statement here reshapes a column.
package db

import (
	"database/sql"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// Q is what a statement runs on: a *sql.DB or a *sql.Tx.
type Q interface {
	Exec(query string, args ...any) (sql.Result, error)
	Query(query string, args ...any) (*sql.Rows, error)
	QueryRow(query string, args ...any) *sql.Row
}

// Human is the founder's name on the board (db.HUMAN).
const Human = "human"

// Now is the clock the statements stamp with; tests replace it.
var Now = defaultNow

func defaultNow() int64 { return time.Now().Unix() }

// KV is one key of an event payload; the order given is the order written, as Python's dict keeps it.
type KV struct {
	K string
	V any
}

// AddEvent appends an event stamped now and answers its row id (db.add_event without a dedup key).
func AddEvent(q Q, agent, kind string, payload ...KV) (int64, error) {
	return addEvent(q, agent, "", kind, "", 0, payload)
}

// AddEventFor is AddEvent with the account the event belongs to.
func AddEventFor(q Q, agent, account, kind string, payload ...KV) (int64, error) {
	return addEvent(q, agent, account, kind, "", 0, payload)
}

// AddEventAt is AddEvent stamped ts.
func AddEventAt(q Q, ts int64, agent, kind string, payload ...KV) (int64, error) {
	return addEvent(q, agent, "", kind, "", ts, payload)
}

// AddEventDeduped appends an event once for a dedup key; 0 means a row with the key was already there.
func AddEventDeduped(q Q, agent, kind, dedup string, payload ...KV) (int64, error) {
	return addEvent(q, agent, "", kind, dedup, 0, payload)
}

func addEvent(q Q, agent, account, kind, dedup string, ts int64, payload []KV) (int64, error) {
	var p, d, acct any
	if len(payload) > 0 {
		parts := make([]string, len(payload))
		for i, e := range payload {
			parts[i] = rpc.PyJSON(e.K, false, false) + ": " + rpc.PyJSON(e.V, false, false)
		}
		p = "{" + strings.Join(parts, ", ") + "}"
	}
	if dedup != "" {
		d = dedup
	}
	if account != "" {
		acct = account
	}
	if ts == 0 {
		ts = Now()
	}
	res, err := q.Exec("INSERT INTO events(ts, agent, account, kind, payload, dedup_key) VALUES(?,?,?,?,?,?) ON CONFLICT(dedup_key) DO NOTHING",
		ts, agent, acct, kind, p, d)
	if err != nil {
		return 0, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return 0, nil
	}
	return res.LastInsertId()
}

// Meta is db.get_meta: the value under name, and whether the row exists (a stored NULL reads as "", true).
func Meta(q Q, name string) (string, bool, error) {
	var v sql.NullString
	err := q.QueryRow("SELECT value FROM meta WHERE name = ?", name).Scan(&v)
	if err == sql.ErrNoRows {
		return "", false, nil
	}
	return v.String, err == nil, err
}

// SetMeta is db.set_meta; an empty value writes NULL, which Python's None does.
func SetMeta(q Q, name, value string) error {
	var v any
	if value != "" {
		v = value
	}
	_, err := q.Exec("INSERT INTO meta(name, value, updated) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
		name, v, Now())
	return err
}

// MetaNames is db.meta_names: the names that start with prefix.
func MetaNames(q Q, prefix string) ([]string, error) {
	rows, err := q.Query("SELECT name FROM meta WHERE substr(name, 1, ?) = ?", len([]rune(prefix)), prefix)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			return nil, err
		}
		out = append(out, n)
	}
	return out, rows.Err()
}

// ClearMeta is db.clear_meta.
func ClearMeta(q Q, name string) error {
	_, err := q.Exec("DELETE FROM meta WHERE name = ?", name)
	return err
}

// AgentProgressKey is the meta name of an agent's progress note when it has no request or task in hand.
func AgentProgressKey(agent string) string { return "progress-agent:" + agent }

// Cursor is db.get_cursor: the row id under name, 0 when unset.
func Cursor(q Q, name string) (int64, error) {
	var id int64
	err := q.QueryRow("SELECT row_id FROM cursors WHERE name = ?", name).Scan(&id)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return id, err
}

// SetCursor is db.set_cursor.
func SetCursor(q Q, name string, id int64) error {
	_, err := q.Exec("INSERT INTO cursors(name, row_id, updated) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET row_id=excluded.row_id, updated=excluded.updated",
		name, id, Now())
	return err
}

// CursorUpdated is db.cursor_updated: when the cursor was last written, and whether it is set.
func CursorUpdated(q Q, name string) (int64, bool, error) {
	var at int64
	err := q.QueryRow("SELECT updated FROM cursors WHERE name = ?", name).Scan(&at)
	if err == sql.ErrNoRows {
		return 0, false, nil
	}
	return at, err == nil, err
}

// ClearCursor is db.clear_cursor.
func ClearCursor(q Q, name string) error {
	_, err := q.Exec("DELETE FROM cursors WHERE name = ?", name)
	return err
}

// MarkToldAhead is db.mark_told_ahead: request rid was told to agent out of turn. The marks of requests not in keep are dropped.
func MarkToldAhead(q Q, agent string, rid int64, keep []int64) error {
	rows, err := q.Query("SELECT name, row_id FROM cursors WHERE name LIKE ?", "ahead:"+agent+":%")
	if err != nil {
		return err
	}
	var drop []string
	for rows.Next() {
		var name string
		var id int64
		if err := rows.Scan(&name, &id); err != nil {
			rows.Close()
			return err
		}
		kept := false
		for _, k := range keep {
			kept = kept || k == id
		}
		if !kept {
			drop = append(drop, name)
		}
	}
	rows.Close()
	for _, name := range drop {
		if err := ClearCursor(q, name); err != nil {
			return err
		}
	}
	return SetCursor(q, aheadKey(agent, rid), rid)
}

func aheadKey(agent string, rid int64) string { return "ahead:" + agent + ":" + itoa(rid) }

// ToldAhead is db.told_ahead.
func ToldAhead(q Q, agent string, rid int64) (bool, error) {
	id, err := Cursor(q, aheadKey(agent, rid))
	return id == rid, err
}

// ForgetTold is db.forget_told: the head is announced again to whatever session runs next.
func ForgetTold(q Q, agent string) error {
	if _, err := q.Exec("DELETE FROM cursors WHERE name = ? OR name LIKE ?", "notify:"+agent, "ahead:"+agent+":%"); err != nil {
		return err
	}
	_, err := q.Exec("UPDATE agent_state SET notified_session = NULL WHERE agent = ?", agent)
	return err
}

// NotifiedSession is db.get_notified_session.
func NotifiedSession(q Q, agent string) (string, error) {
	var s sql.NullString
	err := q.QueryRow("SELECT notified_session FROM agent_state WHERE agent = ?", agent).Scan(&s)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return s.String, err
}

// SetNotifiedSession is db.set_notified_session.
func SetNotifiedSession(q Q, agent, session string) error {
	var s any
	if session != "" {
		s = session
	}
	_, err := q.Exec("UPDATE agent_state SET notified_session = ? WHERE agent = ?", s, agent)
	return err
}
