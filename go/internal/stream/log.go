package stream

import (
	"database/sql"
	"encoding/json"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
)

const (
	// RetentionS is how long a row is kept; KeepRows how many at most. Prune drops what is past either.
	RetentionS = 7 * 24 * 3600
	KeepRows   = 50000
	// ReplayMax is the most a resume may be behind and still be replayed in full; one more is a gap.
	ReplayMax = 5000
	// PrunedCursor is the cursor naming the newest id retention has removed.
	PrunedCursor = "stream:pruned"
)

// Event is one row of stream_log as a client sees it (the spec's Event).
type Event struct {
	Cursor int64           `json:"cursor"`
	Topic  string          `json:"topic"`
	Type   string          `json:"type"`
	At     string          `json:"at"`
	Actor  json.RawMessage `json:"actor"`
	Agent  *string         `json:"agent"`
	Title  *string         `json:"title"`
	Target json.RawMessage `json:"target"`
	Data   json.RawMessage `json:"data"`
}

// Entry is what a writer appends. Data is the event's payload and must be a JSON object; it must carry no credential.
type Entry struct {
	Topic, Type string
	Data        any
	Agent       string // "" is no agent: every subscriber sees it
	Actor       map[string]any
	Title       string
	Target      map[string]any
}

// Log is the stream_log table. The id is the cursor; the Python daemon appends to the same table, and the ids serialise the writers.
type Log struct {
	DB  *sql.DB
	Now func() time.Time // nil is time.Now
}

func (l *Log) now() time.Time {
	if l.Now != nil {
		return l.Now()
	}
	return time.Now()
}

// Stamp is a row's time as an event shows it.
func Stamp(epoch int64) string { return time.Unix(epoch, 0).UTC().Format("2006-01-02T15:04:05Z") }

const cols = "id, ts, topic, type, agent, actor, title, target, data"

type scanner interface{ Scan(...any) error }

func eventOf(r scanner) (*Event, error) {
	var (
		ts                        int64
		agent, actor, title, targ sql.NullString
		data                      string
		e                         = &Event{}
	)
	if err := r.Scan(&e.Cursor, &ts, &e.Topic, &e.Type, &agent, &actor, &title, &targ, &data); err != nil {
		return nil, err
	}
	e.At = Stamp(ts)
	if agent.Valid {
		e.Agent = &agent.String
	}
	if title.Valid {
		e.Title = &title.String
	}
	if actor.Valid && actor.String != "" {
		e.Actor = json.RawMessage(actor.String)
	}
	if targ.Valid && targ.String != "" {
		e.Target = json.RawMessage(targ.String)
	}
	e.Data = json.RawMessage(data)
	return e, nil
}

func (l *Log) events(query string, args ...any) ([]*Event, error) {
	rows, err := l.DB.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Event
	for rows.Next() {
		e, err := eventOf(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

func marks(n int) string { return strings.TrimSuffix(strings.Repeat("?,", n), ",") }

func optional(m map[string]any) any {
	if len(m) == 0 {
		return nil
	}
	b, _ := json.Marshal(m)
	return string(b)
}

// Append writes one event and returns it as the log now holds it.
func (l *Log) Append(e Entry) (*Event, error) {
	data, err := json.Marshal(e.Data)
	if err != nil {
		return nil, err
	}
	var agent, title any
	if e.Agent != "" {
		agent = e.Agent
	}
	if e.Title != "" {
		title = e.Title
	}
	res, err := l.DB.Exec("INSERT INTO stream_log(ts, topic, type, agent, actor, title, target, data) VALUES(?,?,?,?,?,?,?,?)",
		l.now().Unix(), e.Topic, e.Type, agent, optional(e.Actor), title, optional(e.Target), string(data))
	if err != nil {
		return nil, err
	}
	id, err := res.LastInsertId()
	if err != nil {
		return nil, err
	}
	return eventOf(l.DB.QueryRow("SELECT "+cols+" FROM stream_log WHERE id = ?", id))
}

// Newest is the highest cursor, 0 for an empty log.
func (l *Log) Newest() (int64, error) {
	var n int64
	err := l.DB.QueryRow("SELECT COALESCE(MAX(id), 0) FROM stream_log").Scan(&n)
	return n, err
}

// PrunedThrough is the newest cursor retention has removed.
func (l *Log) PrunedThrough() (int64, error) { return db.Cursor(l.DB, PrunedCursor) }

// Since is the events of the topics after cursor, oldest first, at most limit; agent keeps this agent's and those of no agent.
func (l *Log) Since(cursor int64, topics []string, agent string, limit int) ([]*Event, error) {
	q := "SELECT " + cols + " FROM stream_log WHERE id > ? AND topic IN (" + marks(len(topics)) + ")"
	args := []any{cursor}
	for _, t := range topics {
		args = append(args, t)
	}
	if agent != "" {
		q += " AND (agent = ? OR agent IS NULL)"
		args = append(args, agent)
	}
	return l.events(q+" ORDER BY id LIMIT ?", append(args, limit)...)
}

// Prune drops what is past retention and remembers how far, so a client resuming from before it is told.
func (l *Log) Prune() error {
	var old, over int64
	if err := l.DB.QueryRow("SELECT COALESCE(MAX(id), 0) FROM stream_log WHERE ts < ?", l.now().Unix()-RetentionS).Scan(&old); err != nil {
		return err
	}
	if err := l.DB.QueryRow("SELECT COALESCE(MIN(id), 0) FROM (SELECT id FROM stream_log ORDER BY id DESC LIMIT ?)", KeepRows).Scan(&over); err != nil {
		return err
	}
	through := max(old, over-1, 0)
	done, err := l.PrunedThrough()
	if err != nil || through <= done {
		return err
	}
	tx, err := l.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.Exec("DELETE FROM stream_log WHERE id <= ?", through); err != nil {
		return err
	}
	if err := db.SetCursor(tx, PrunedCursor, through); err != nil {
		return err
	}
	return tx.Commit()
}
