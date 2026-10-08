// Package transcript numbers a structured session's events per agent, keeps them in the database and serves them as pages
// (contract/protocol.md, "Transcript stream"). The sink is what the structured package emits into; reads and the topic take the
// rows from here.
package transcript

import (
	"database/sql"
	"encoding/json"
	"time"
)

// Keep is how many events per agent are kept; older ones go and first_seq moves up, which a reader sees as a gap.
const Keep = 20000

// ToolResultMax is the longest tool output an event carries.
const ToolResultMax = 64 << 10

// Item sources of chat.transcript's ids, and the id itself: the time in seconds, a source, and a number within it.
const (
	ItemReply  = 0
	ItemPrompt = 1
)

func ItemID(at, source, n int64) int64 { return at*1_000_000 + source*100_000 + n%100_000 }

// Event is one row, as the topic and transcript.events show it.
type Event struct {
	Agent   string
	Seq     int64
	Session string
	Turn    string // "" is null
	At      int64
	Kind    string
	Item    *int64
	Body    map[string]any // the field named like the kind, or text
}

// JSON is the spec's TranscriptEvent.
func (e Event) JSON() map[string]any {
	out := map[string]any{"agent": e.Agent, "session": e.Session, "seq": e.Seq, "turn": nil, "at": time.Unix(e.At, 0).UTC().Format("2006-01-02T15:04:05Z"),
		"kind": e.Kind, "item": nil}
	if e.Turn != "" {
		out["turn"] = e.Turn
	}
	if e.Item != nil {
		out["item"] = *e.Item
	}
	for k, v := range e.Body {
		out[k] = v
	}
	return out
}

// Page is the answer to transcript.events.
type Page struct {
	Events      []Event
	More        bool
	First, Last int64
	Gap         bool
}

// Read pages agent's events. since reads the ones above a seq, oldest first; before the page just below a seq; neither the newest page.
func Read(db *sql.DB, agent string, since, before *int64, limit int) (Page, error) {
	var p Page
	var first, last sql.NullInt64
	if err := db.QueryRow("SELECT MIN(seq), MAX(seq) FROM transcript_events WHERE agent = ?", agent).Scan(&first, &last); err != nil {
		return p, err
	}
	p.First, p.Last = first.Int64, last.Int64
	var rows *sql.Rows
	var err error
	cols := "SELECT agent, seq, session, turn, at, kind, item, body FROM transcript_events WHERE agent = ?"
	switch {
	case since != nil:
		p.Gap = p.First > 0 && *since < p.First-1
		rows, err = db.Query(cols+" AND seq > ? ORDER BY seq LIMIT ?", agent, *since, limit+1)
	case before != nil:
		rows, err = db.Query(cols+" AND seq < ? ORDER BY seq DESC LIMIT ?", agent, *before, limit+1)
	default:
		rows, err = db.Query(cols+" ORDER BY seq DESC LIMIT ?", agent, limit+1)
	}
	if err != nil {
		return p, err
	}
	defer rows.Close()
	for rows.Next() {
		var e Event
		var turn sql.NullString
		var item sql.NullInt64
		var body string
		if err := rows.Scan(&e.Agent, &e.Seq, &e.Session, &turn, &e.At, &e.Kind, &item, &body); err != nil {
			return p, err
		}
		e.Turn = turn.String
		if item.Valid {
			e.Item = &item.Int64
		}
		json.Unmarshal([]byte(body), &e.Body)
		p.Events = append(p.Events, e)
	}
	if err := rows.Err(); err != nil {
		return p, err
	}
	if p.More = len(p.Events) > limit; p.More {
		p.Events = p.Events[:limit]
	}
	if since == nil { // read newest first
		for i, j := 0, len(p.Events)-1; i < j; i, j = i+1, j-1 {
			p.Events[i], p.Events[j] = p.Events[j], p.Events[i]
		}
	}
	return p, nil
}

// Replies are agent's text events as chat.transcript's reply items, newest first up to limit: id, time and text.
func Replies(db *sql.DB, agent string, limit int) ([]Reply, error) {
	rows, err := db.Query("SELECT item, at, body FROM transcript_events WHERE agent = ? AND kind = 'text' AND item IS NOT NULL ORDER BY seq DESC LIMIT ?", agent, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Reply
	for rows.Next() {
		var r Reply
		var body string
		if err := rows.Scan(&r.ID, &r.At, &body); err != nil {
			return nil, err
		}
		var b struct {
			Text string `json:"text"`
		}
		json.Unmarshal([]byte(body), &b)
		r.Text = b.Text
		out = append(out, r)
	}
	return out, rows.Err()
}

type Reply struct {
	ID, At int64
	Text   string
}
