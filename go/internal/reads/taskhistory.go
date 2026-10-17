package reads

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
)

// historyKinds are the events a task's writes keep, in the same transaction as the change: they are the task's history.
var historyKinds = []string{"task_new", "task_begin", "edit_task", "block_task", "unblock_task", "queue_task", "draft_task", "task_start",
	"task_handoff", "task_report", "accept_task", "reject_task", "mark_task_done", "discard_task", "close_task", "reopen_task", "release_task", "merge_task",
	"memo_add", "memo_edit", "memo_delete"}

// editFields is how an edit_task event's keys read as fields, in the order a row lists them: the owner column is who works the task (its
// assignee), the reporter who answers for it (its owner).
var editFields = [][2]string{{"owner", "assignee"}, {"reporter", "owner"}, {"title", "title"}, {"body", "body"}, {"priority", "priority"},
	{"timeout", "timeout"}}

// historyState is the state an event leaves the task in: chat's stateAfter, and the three only the history shows.
func historyState(kind string) (string, bool) {
	if s, ok := stateAfter[kind]; ok {
		return s, true
	}
	s, ok := map[string]string{"task_new": "opened", "release_task": "released", "merge_task": "merged"}[kind]
	return s, ok
}

// taskHistory is task.history: every change to a task, one row per field changed (who, field, was, now, when), newest first. It is read
// from the events the task's writes keep, so there is one history: task.get's assignments read the same events. A state row's was is the
// state before it. Nothing here is ever rewritten or removed: a fix is a later row.
func (e *Env) taskHistory(c *rpc.Conn, params map[string]any) (any, error) {
	tid, err := optInt(params, "task")
	if err != nil || tid == nil {
		return nil, badParam("task is a task number", "task")
	}
	t, err := queryOne(e.DB, "SELECT id, title FROM tasks WHERE id = ?", *tid)
	if err != nil {
		return nil, err
	}
	if t == nil {
		nf := rpc.NotFoundError(fmt.Sprintf("no task T%d", *tid))
		nf.Data = map[string]any{"what": "task", "id": *tid}
		return nil, nf
	}
	events, err := query(e.DB, "SELECT ts, agent, kind, payload FROM events WHERE kind IN ('"+strings.Join(historyKinds, "', '")+"') AND "+
		eventTask+" = ? ORDER BY id", *tid)
	if err != nil {
		return nil, err
	}
	title := t.str("title") // the title it was opened with: the first edit's old title, else the one it still has
	for _, ev := range events {
		if ev.str("kind") != "edit_task" {
			continue
		}
		if c, ok := decodeObject(ev.str("payload"))["title"].(map[string]any); ok {
			title = text(c["old"])
			break
		}
	}
	var rows []map[string]any
	state := ""
	for _, ev := range events {
		p, kind, before := decodeObject(ev.str("payload")), ev.str("kind"), len(rows)
		add := func(field, was, now string) map[string]any {
			r := map[string]any{"at": ts(ev["ts"]), "by": ev.str("agent"), "field": field, "was": was, "now": now}
			rows = append(rows, r)
			return r
		}
		note := func(r map[string]any, key string) {
			if s := text(p[key]); s != "" {
				r["note"] = s
			}
		}
		switch kind {
		case "task_new":
			add("title", "", title)
			add("assignee", "", text(p["owner"]))
		case "task_begin":
			add("title", "", title)
			add("assignee", "", ev.str("agent"))
		case "edit_task":
			for _, f := range editFields {
				if c, ok := p[f[0]].(map[string]any); ok {
					add(f[1], text(c["old"]), text(c["new"]))
				}
			}
		case "task_handoff":
			note(add("assignee", text(p["from"]), text(p["to"])), "note")
		case "block_task":
			add("blocked_by", "", fmt.Sprintf("T%d", int64(seconds(p["blocks_on"]))))
		case "unblock_task":
			add("blocked_by", fmt.Sprintf("T%d", int64(seconds(p["blocks_on"]))), "")
		case "merge_task":
			add("merged_into", "", fmt.Sprintf("T%d", int64(seconds(p["into"]))))
		case "memo_add", "memo_edit", "memo_delete":
			add("memo", text(p["was"]), text(p["now"]))["memo"] = int64(seconds(p["memo"]))
		}
		now, ok := historyState(kind)
		if !ok && state == "answered" && len(rows) > before && rows[len(rows)-1]["field"] == "assignee" {
			now, ok = "queued", true // a reassign ends an answer: the task waits on its new holder
		}
		if ok {
			r := add("state", state, now)
			for _, key := range []string{"done", "report", "reason", "note"} {
				note(r, key)
			}
			state = now
		}
	}
	out := make([]any, len(rows))
	for i, r := range rows {
		out[len(rows)-1-i] = r
	}
	return map[string]any{"task": *tid, "rows": out}, nil
}

// text is a payload value as text: "" for none.
func text(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
