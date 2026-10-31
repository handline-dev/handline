package watcher

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
	"github.com/handline/handline/go/internal/team"
)

// Notifications is the notifications topic, from what the other writers leave: a task marked done and an account stalled (the events
// table) and a prompt an agent opened (prompt_blocks). The places reached are cursors in the database, so what happened while the daemon was
// down is said when it is back, and one set before the stream existed starts at the newest row: history is not news. The stall
// notification is the agents detector's.
type Notifications struct {
	Reads *reads.Env
}

func (*Notifications) Name() string { return "notifications" }

func (*Notifications) Tables() []string {
	return []string{"cursors", "events", "prompt_blocks", "tasks"}
}

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

// resultCap is how many characters of the done evidence's first non-blank line a task_done carries.
const resultCap = 140

// taskDone is a task_done's data: the task, and when the store has it its title, the first non-blank line of its done evidence, who the task
// answers to (reporter: its reporter, else its opener) and whether the founder gave it (from_founder: the founder opened it or answers for it).
func taskDone(q *sql.DB, task any) (map[string]any, error) {
	data := map[string]any{"task": task}
	var title, evidence, reporter, opener sql.NullString
	err := q.QueryRow("SELECT title, done_evidence, reporter, opened_by FROM tasks WHERE id = ?", fmt.Sprint(task)).Scan(&title, &evidence, &reporter, &opener)
	if err == sql.ErrNoRows {
		return data, nil
	}
	if err != nil {
		return nil, err
	}
	if title.String != "" {
		data["task_title"] = title.String
	}
	asker := reporter.String
	if asker == "" {
		asker = opener.String
	}
	if asker != "" {
		data["reporter"] = asker
		data["from_founder"] = asker == "human" || opener.String == "human"
	}
	first, _, _ := strings.Cut(strings.TrimSpace(evidence.String), "\n")
	if r := []rune(strings.TrimSpace(first)); len(r) > 0 {
		if len(r) > resultCap {
			r = r[:resultCap]
		}
		data["result"] = string(r)
	}
	return data, nil
}

func (n *Notifications) Scan(q *sql.DB, emit Emit) error {
	last, err := Prime(q, eventsCursor, "SELECT COALESCE(MAX(id), 0) FROM events")
	if err != nil {
		return err
	}
	rows, err := q.Query("SELECT id, agent, account, kind, payload FROM events WHERE id > ? AND kind IN ('mark_task_done', 'login_expired', 'disk_low') ORDER BY id", last)
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
	if err := rows.Err(); err != nil {
		rows.Close()
		return err
	}
	rows.Close()
	for _, e := range events {
		payload := payloadOf(e.body)
		if e.kind == "mark_task_done" {
			task, named := payload["task_id"]
			if done, has := payload["done"]; (!has || truthy(done)) && named && task != nil {
				id := fmt.Sprint(task)
				data, err := taskDone(q, task)
				if err != nil {
					return err
				}
				if data["reporter"] != "human" {
					continue // the founder hears of the tasks they answer for, and no others
				}
				if _, err := emit(stream.Entry{Topic: "notifications", Type: "task_done", Data: data, Agent: e.agent,
					Title: "task " + id + " done", Target: map[string]any{"screen": "task", "id": id}}); err != nil {
					return err
				}
			}
		} else if e.kind == "disk_low" {
			free, _ := payload["free_bytes"].(json.Number).Int64()
			total, _ := payload["total_bytes"].(json.Number).Int64()
			if _, err := emit(stream.Entry{Topic: "notifications", Type: "disk_low", Data: map[string]any{"free_bytes": free, "total_bytes": total},
				Title: fmt.Sprintf("disk low: %d GB left", free>>30)}); err != nil {
				return err
			}
		} else {
			acct, _ := payload["account"].(string)
			if acct == "" {
				acct = e.account.String
			}
			// A refused login keeps Python's words (the parity test holds both daemons to them); any other stall says its reason.
			title := acct + " needs logging in"
			if why, _ := payload["error"].(string); why != "" && why != "authentication_failed" {
				title = acct + " stalled: " + team.StallWord(why)
			}
			if _, err := emit(stream.Entry{Topic: "notifications", Type: "account_out", Data: map[string]any{"account": acct}, Agent: e.agent,
				Title: title, Target: map[string]any{"screen": "account", "id": acct}}); err != nil {
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
	if err := prows.Err(); err != nil {
		prows.Close()
		return err
	}
	prows.Close()
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
