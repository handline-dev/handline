package transcript

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/structured"
)

// runningTurn is a running turn: the message that began it, and the session it runs in.
type runningTurn struct{ uuid, session string }

// Sink is the structured.EventSink of the daemon: it turns the session's events into the contract's kinds, numbers them per agent (from 1,
// by 1, across sessions, never reused) and writes each in one transaction with the number it took. Publish gets the event after it is
// committed, in order.
type Sink struct {
	DB  *sql.DB
	Now func() time.Time
	// Session is the id of agent's running session; nil is "".
	Session func(agent string) string
	// Prompt is the spec's Prompt of the prompt row block, and when the row was first seen; false when there is none.
	Prompt func(agent string, block int64) (prompt map[string]any, firstSeen int64, ok bool)
	// Publish is called with each committed event; nil publishes nothing.
	Publish func(Event)
	// Err gets what could not be written; nil drops it.
	Err func(agent string, err error)

	mu    sync.Mutex             // guards the two maps; an agent's events are serialised by its own lock, so one agent's write never waits for another's
	turns map[string]runningTurn // agent -> the turn that is running
	locks map[string]*sync.Mutex
}

// lockAgent takes agent's lock and returns the func that releases it.
func (s *Sink) lockAgent(agent string) func() {
	s.mu.Lock()
	if s.locks == nil {
		s.locks = map[string]*sync.Mutex{}
	}
	l := s.locks[agent]
	if l == nil {
		l = &sync.Mutex{}
		s.locks[agent] = l
	}
	s.mu.Unlock()
	l.Lock()
	return l.Unlock
}

func (s *Sink) turnOf(agent string) (runningTurn, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.turns[agent]
	return t, ok
}

func (s *Sink) setTurn(agent string, t runningTurn) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.turns == nil {
		s.turns = map[string]runningTurn{}
	}
	s.turns[agent] = t
}

func (s *Sink) dropTurn(agent string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.turns, agent)
}

func (s *Sink) now() int64 {
	if s.Now != nil {
		return s.Now().Unix()
	}
	return time.Now().Unix()
}

// Emit takes one of the session's events.
func (s *Sink) Emit(agent string, ev structured.Event) {
	defer s.lockAgent(agent)()
	session := ""
	if s.Session != nil {
		session = s.Session(agent)
	}
	// A turn belongs to its session: a new session id means the old one ended without saying so, and its turn is over.
	if t, ok := s.turnOf(agent); ok && t.session != session {
		s.put(agent, t.session, s.endTurn(agent, "interrupted"))
	}
	for _, e := range s.convert(agent, session, ev) {
		s.put(agent, session, e)
	}
}

// put numbers, stores and publishes one event.
func (s *Sink) put(agent, session string, e Event) {
	e.Agent, e.At, e.Session = agent, s.now(), session
	if err := s.write(&e); err != nil {
		if s.Err != nil {
			s.Err(agent, err)
		}
		return
	}
	if s.Publish != nil {
		s.Publish(e)
	}
}

// endTurn closes agent's running turn with a turn_end of this reason; the zero Event when none runs.
func (s *Sink) endTurn(agent, reason string) Event {
	t, ok := s.turnOf(agent)
	if !ok {
		return Event{}
	}
	s.dropTurn(agent)
	return Event{Turn: t.uuid, Kind: "turn_end", Body: map[string]any{"turn_end": map[string]any{"reason": reason, "cost_usd": 0.0, "duration_ms": int64(0), "usage": usageOf(nil)}}}
}

// convert maps the session's event to the contract's. A frame with nothing for a view is dropped.
func (s *Sink) convert(agent, session string, ev structured.Event) []Event {
	d := ev.Data
	cur, _ := s.turnOf(agent)
	turn := cur.uuid
	switch ev.Type {
	case "message":
		if turn != "" { // joined the running turn: not a new one
			return nil
		}
		uuid, _ := d["uuid"].(string)
		text, _ := d["text"].(string)
		s.setTurn(agent, runningTurn{uuid, session})
		return []Event{{Turn: uuid, Kind: "turn_start", Body: map[string]any{"turn_start": map[string]any{"message": uuid, "from": s.sender(agent, uuid), "text": text}}}}
	case "assistant_text":
		text, _ := d["text"].(string)
		return []Event{{Turn: turn, Kind: "text", Body: map[string]any{"text": text}}}
	case "tool_use":
		input, _ := d["input"].(map[string]any)
		if input == nil {
			input = map[string]any{}
		}
		return []Event{{Turn: turn, Kind: "tool_use", Body: map[string]any{"tool_use": map[string]any{"id": d["id"], "tool": d["name"], "input": input}}}}
	case "tool_result":
		full := flatten(d["content"])
		text, cut := cutText(full, ToolResultMax)
		ok, _ := d["is_error"].(bool)
		result := map[string]any{"tool_use_id": d["tool_use_id"], "ok": !ok, "text": text, "truncated": cut}
		if cut {
			result["dropped_lines"] = strings.Count(full[len(text):], "\n")
		}
		return []Event{{Turn: turn, Kind: "tool_result", Body: map[string]any{"tool_result": result}}}
	case "permission_request":
		block, _ := d["block"].(int64)
		if s.Prompt == nil {
			return nil
		}
		prompt, firstSeen, ok := s.Prompt(agent, block)
		if !ok {
			return nil
		}
		item := ItemID(firstSeen, ItemPrompt, block)
		return []Event{{Turn: turn, Kind: "permission", Item: &item, Body: map[string]any{"permission": map[string]any{"tool_use_id": d["tool_use"], "prompt": prompt}}}}
	case "permission_answer":
		behavior, by := "deny", str(d["by"])
		if allow, _ := d["allow"].(bool); allow {
			behavior = "allow"
		}
		switch by {
		case "policy", "cancel", "closed":
		default:
			by = "person"
		}
		var rule, msg any
		if r := str(d["rule"]); r != "" {
			rule = r
		}
		if m := str(d["said"]); m != "" {
			msg = m
		}
		return []Event{{Turn: turn, Kind: "permission_answer", Body: map[string]any{"permission_answer": map[string]any{
			"tool_use_id": d["tool_use"], "behavior": behavior, "by": by, "rule": rule, "message": msg}}}}
	case "turn_end":
		s.dropTurn(agent)
		reason := "done"
		if sub := str(d["subtype"]); d["is_error"] == true || (sub != "" && sub != "success") {
			reason = "error"
			if strings.Contains(sub, "interrupt") {
				reason = "interrupted"
			}
		}
		return []Event{{Turn: turn, Kind: "turn_end", Body: map[string]any{"turn_end": map[string]any{
			"reason": reason, "cost_usd": number(d["cost_usd"]), "duration_ms": int64(number(d["duration_ms"])), "usage": usageOf(d["usage"])}}}}
	case "error":
		out := []Event{{Turn: turn, Kind: "error", Body: map[string]any{"error": map[string]any{"code": str(d["code"]), "text": str(d["text"])}}}}
		if end := s.endTurn(agent, "error"); end.Kind != "" { // the turn the failure cut off ends with it
			out = append(out, end)
		}
		return out
	case "session_end": // the session is over, however: a turn it left open is interrupted
		if end := s.endTurn(agent, "interrupted"); end.Kind != "" {
			return []Event{end}
		}
		return nil
	}
	return nil // state: the working state is read from the agent's view, not carried here
}

// write numbers e and stores it, with the item id it carries, in one transaction.
func (s *Sink) write(e *Event) (err error) {
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			tx.Rollback()
		}
	}()
	last, err := db.Cursor(tx, "transcript_seq:"+e.Agent)
	if err != nil {
		return err
	}
	e.Seq = last + 1
	if err = db.SetCursor(tx, "transcript_seq:"+e.Agent, e.Seq); err != nil {
		return err
	}
	if e.Kind == "text" { // a reply item of chat.transcript, drawn from this row
		item := ItemID(e.At, ItemReply, e.Seq*2)
		e.Item = &item
	}
	body, err := json.Marshal(e.Body)
	if err != nil {
		return err
	}
	var turn, item any
	if e.Turn != "" {
		turn = e.Turn
	}
	if e.Item != nil {
		item = *e.Item
	}
	if _, err = tx.Exec("INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES(?,?,?,?,?,?,?,?)",
		e.Agent, e.Seq, e.Session, turn, e.At, e.Kind, item, string(body)); err != nil {
		return err
	}
	if e.Seq%500 == 0 {
		if _, err = tx.Exec("DELETE FROM transcript_events WHERE agent = ? AND seq <= ?", e.Agent, e.Seq-Keep); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// sender is who sent the message with this uuid: the requester of the request it told, "daemon" for a notice, nil when unknown.
func (s *Sink) sender(agent, uuid string) any {
	var id string
	err := s.DB.QueryRow("SELECT id FROM operations WHERE kind = 'send' AND agent = ? AND result_json LIKE ?", agent, `%"uuid":"`+uuid+`"%`).Scan(&id)
	if err != nil {
		return nil
	}
	op := strings.TrimPrefix(id, "send:"+agent+":")
	switch {
	case strings.HasPrefix(op, "request:"):
		first := strings.SplitN(strings.TrimPrefix(op, "request:"), ",", 2)[0]
		var from string
		if s.DB.QueryRow("SELECT requester FROM requests WHERE id = ?", first).Scan(&from) == nil {
			return from
		}
		return nil
	case strings.HasPrefix(op, "notice:"), strings.HasPrefix(op, "answers:"), strings.HasPrefix(op, "cancelled:"):
		return "daemon"
	}
	return nil
}

func str(v any) string { s, _ := v.(string); return s }

func number(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int64:
		return float64(n)
	case int:
		return float64(n)
	}
	return 0
}

func usageOf(v any) map[string]any {
	u, _ := v.(map[string]any)
	n := func(k string) int64 { return int64(number(u[k])) }
	return map[string]any{"input": n("input_tokens"), "output": n("output_tokens"), "cache_read": n("cache_read_input_tokens"), "cache_write": n("cache_creation_input_tokens")}
}

// flatten is a tool result's content as text: a string, or the text blocks of a list.
func flatten(v any) string {
	switch c := v.(type) {
	case string:
		return c
	case []any:
		var parts []string
		for _, b := range c {
			if m, ok := b.(map[string]any); ok {
				if t, ok := m["text"].(string); ok {
					parts = append(parts, t)
				}
			}
		}
		return strings.Join(parts, "\n")
	case nil:
		return ""
	}
	return fmt.Sprint(v)
}

// cutText is s cut to at most max bytes at a character boundary, and whether it was cut.
func cutText(s string, max int) (string, bool) {
	if len(s) <= max {
		return s, false
	}
	s = s[:max]
	for !utf8.ValidString(s) {
		s = s[:len(s)-1]
	}
	return s, true
}
