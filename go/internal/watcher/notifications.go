package watcher

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

// Notifications is the notifications topic, from what the other writers leave: a task marked done and a login that expired (the events
// table) and a prompt an agent opened (prompt_blocks). The places reached are cursors in the database, so what happened while the daemon was
// down is said when it is back, and one set before the stream existed starts at the newest row: history is not news. The stall
// notification is the agents detector's.
type Notifications struct {
	Reads *reads.Env
}

func (*Notifications) Name() string { return "notifications" }

const (
	eventsCursor  = "stream:events"
	promptsCursor = "stream:prompts"
)

// payloadOf is an event's payload when it is a JSON object, else nothing.
func payloadOf(s sql.NullString) map[string]any {
	if !s.Valid || len(s.String) == 0 || s.String[0] != '{' {
		return map[string]any{}
	}
	dec := json.NewDecoder(bytes.NewReader([]byte(s.String)))
	dec.UseNumber()
	var out map[string]any
	if dec.Decode(&out) != nil {
		return map[string]any{}
	}
	return out
}

// truthy is Python's truth of a decoded JSON value.
func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case json.Number:
		f, _ := x.Float64()
		return f != 0
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

func (n *Notifications) Scan(q *sql.DB, emit Emit) error {
	last, err := Prime(q, eventsCursor, "SELECT COALESCE(MAX(id), 0) FROM events")
	if err != nil {
		return err
	}
	rows, err := q.Query("SELECT id, agent, account, kind, payload FROM events WHERE id > ? AND kind IN ('mark_task_done', 'login_expired') ORDER BY id", last)
	if err != nil {
		return err
	}
	type event struct {
		id            int64
		agent, kind   string
		account, body sql.NullString
	}
	var events []event
	for rows.Next() {
		var e event
		if err := rows.Scan(&e.id, &e.agent, &e.account, &e.kind, &e.body); err != nil {
			rows.Close()
			return err
		}
		events = append(events, e)
	}
	if err := rows.Close(); err != nil {
		return err
	}
	for _, e := range events {
		payload := payloadOf(e.body)
		if e.kind == "mark_task_done" {
			task, named := payload["task_id"]
			if done, has := payload["done"]; (!has || truthy(done)) && named && task != nil {
				id := fmt.Sprint(task)
				if _, err := emit(stream.Entry{Topic: "notifications", Type: "task_done", Data: map[string]any{"task": task}, Agent: e.agent,
					Title: "task " + id + " done", Target: map[string]any{"screen": "task", "id": id}}); err != nil {
					return err
				}
			}
		} else {
			acct, _ := payload["account"].(string)
			if acct == "" {
				acct = e.account.String
			}
			if _, err := emit(stream.Entry{Topic: "notifications", Type: "account_out", Data: map[string]any{"account": acct}, Agent: e.agent,
				Title: acct + " needs logging in", Target: map[string]any{"screen": "account", "id": acct}}); err != nil {
				return err
			}
		}
		if err := db.SetCursor(q, eventsCursor, e.id); err != nil { // per row: an emit that fails does not repeat the ones before it
			return err
		}
	}
	seen, err := Prime(q, promptsCursor, "SELECT COALESCE(MAX(id), 0) FROM prompt_blocks")
	if err != nil {
		return err
	}
	prows, err := q.Query("SELECT id, agent, title FROM prompt_blocks WHERE id > ? ORDER BY id", seen)
	if err != nil {
		return err
	}
	type prompt struct {
		id           int64
		agent, title string
	}
	var prompts []prompt
	for prows.Next() {
		var p prompt
		if err := prows.Scan(&p.id, &p.agent, &p.title); err != nil {
			prows.Close()
			return err
		}
		prompts = append(prompts, p)
	}
	if err := prows.Close(); err != nil {
		return err
	}
	for _, p := range prompts {
		if data, _, ok := n.Reads.PromptOf(p.agent, p.id); ok {
			if _, err := emit(stream.Entry{Topic: "notifications", Type: "prompt_asked", Data: data, Agent: p.agent,
				Title: p.agent + " asks: " + p.title, Target: map[string]any{"screen": "agent", "id": p.agent}}); err != nil {
				return err
			}
		}
		if err := db.SetCursor(q, promptsCursor, p.id); err != nil {
			return err
		}
	}
	return nil
}
