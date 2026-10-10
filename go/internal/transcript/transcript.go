// Package transcript numbers a structured session's events per agent, keeps them in the database and serves them as pages
// (contract/protocol.md, "Transcript stream"). The sink is what the structured package emits into; reads and the topic take the
// rows from here.
package transcript

import (
	"database/sql"
	"encoding/json"
	"strconv"
	"strings"
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
	ItemTool   = 3 // 2 is the task events'; a tool item takes its tool_use event's seq
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

// Tool is a tool call as chat.transcript's tool item: the call with its result when one came. Output is nil with no result;
// Dropped is the lines the store cut from the end of the output.
type Tool struct {
	ID, At  int64
	Name    string
	Text    string
	Output  *string
	Dropped int64
}

// ToolWindow is which of an agent's tool calls a page needs: at most Limit of them, the newest with an item id below Before, or with
// Since the oldest from that second on. A zero is no bound. Calls share a second, so Before is held to the id itself: a call the page
// would drop must not use up a slot.
type ToolWindow struct {
	Before, Since int64
	Limit         int
}

// toolItemSQL is a tool_use row's item id (ItemID of its second, ItemTool and seq), as SQL.
var toolItemSQL = "(at * 1000000 + " + strconv.Itoa(ItemTool*100000) + " + seq % 100000)"

// Tools are agent's tool_use events in the window paired by tool_use_id with their tool_result, oldest first. A call with no result yet
// is an item with no output. Only the results of the calls read are read.
func Tools(db *sql.DB, agent string, w ToolWindow) ([]Tool, error) {
	query, args := "SELECT seq, at, body FROM transcript_events WHERE agent = ? AND kind = 'tool_use'", []any{agent}
	order := "DESC"
	if w.Since > 0 {
		query, args, order = query+" AND at >= ?", append(args, w.Since/1_000_000), "ASC"
	} else if w.Before > 0 {
		query, args = query+" AND "+toolItemSQL+" < ?", append(args, w.Before)
	}
	rows, err := db.Query(query+" ORDER BY seq "+order+" LIMIT ?", append(args, w.Limit)...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Tool
	byUse := map[string]int{}
	var ids []any
	oldest := int64(0)
	for rows.Next() {
		var seq, at int64
		var body string
		if err := rows.Scan(&seq, &at, &body); err != nil {
			return nil, err
		}
		var b struct {
			Use struct {
				ID    string         `json:"id"`
				Tool  string         `json:"tool"`
				Input map[string]any `json:"input"`
			} `json:"tool_use"`
		}
		json.Unmarshal([]byte(body), &b)
		byUse[b.Use.ID] = len(out)
		ids = append(ids, b.Use.ID)
		out = append(out, Tool{ID: ItemID(at, ItemTool, seq), At: at, Name: b.Use.Tool, Text: toolText(b.Use.Input)})
		if oldest == 0 || seq < oldest {
			oldest = seq
		}
	}
	if err := rows.Err(); err != nil || len(out) == 0 {
		return out, err
	}
	res, err := db.Query("SELECT body FROM transcript_events WHERE agent = ? AND kind = 'tool_result' AND seq > ? AND json_extract(body, '$.tool_result.tool_use_id') IN ("+
		strings.TrimSuffix(strings.Repeat("?,", len(ids)), ",")+") ORDER BY seq", append([]any{agent, oldest}, ids...)...)
	if err != nil {
		return nil, err
	}
	defer res.Close()
	for res.Next() {
		var body string
		if err := res.Scan(&body); err != nil {
			return nil, err
		}
		var b struct {
			Result struct {
				ID      string `json:"tool_use_id"`
				Text    string `json:"text"`
				Cut     bool   `json:"truncated"`
				Dropped int64  `json:"dropped_lines"`
			} `json:"tool_result"`
		}
		json.Unmarshal([]byte(body), &b)
		if i, ok := byUse[b.Result.ID]; ok && out[i].Output == nil {
			text := b.Result.Text
			out[i].Output, out[i].Dropped = &text, b.Result.Dropped
			if b.Result.Cut && out[i].Dropped < 1 {
				out[i].Dropped = 1 // a row written before the count was kept: a cut output never reads as whole
			}
		}
	}
	if err := res.Err(); err != nil {
		return nil, err
	}
	if w.Since == 0 { // read newest first: oldest first, as every other source
		for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
			out[i], out[j] = out[j], out[i]
		}
	}
	return out, nil
}

// FitOutput is text cut to at most max bytes at a character boundary, and the lines the cut left out (0 when nothing was).
func FitOutput(text string, max int) (string, int64) {
	kept, cut := cutText(text, max)
	if !cut {
		return text, 0
	}
	return kept, int64(linesIn(text[len(kept):]))
}

// toolText is what a tool call was asked to do, in one string: the command, else the path, pattern, address or description.
func toolText(input map[string]any) string {
	for _, k := range []string{"command", "file_path", "path", "pattern", "url", "query", "description", "prompt"} {
		if v, ok := input[k].(string); ok && v != "" {
			return v
		}
	}
	return ""
}
