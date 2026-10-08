package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// idParam is a task or request number under key (rpc_tasks._task_id).
func idParam(p map[string]any, key string) (int64, error) {
	n, ok := rpc.AsInt(p[key])
	if !ok {
		return 0, badParam(key+" is a task number", key)
	}
	return n, nil
}

// parsePriority is actions.parse_priority: P1, p2 or a bare 3.
func parsePriority(v any) (int64, error) {
	raw := fmt.Sprint(v)
	t := strings.ToUpper(strings.TrimSpace(raw))
	t = strings.TrimPrefix(t, "P")
	if t == "1" || t == "2" || t == "3" {
		return int64(t[0] - '0'), nil
	}
	return 0, badParam(fmt.Sprintf("'%s' is not a priority - write P1 (high), P2 (default) or P3 (low)", raw), "")
}

// parseExpects is actions.parse_expects: what task.done must be given to close the task.
func parseExpects(v any) (string, error) {
	raw := fmt.Sprint(v)
	t := strings.ToLower(strings.TrimSpace(raw))
	switch t {
	case "sha", "verdict", "decision", "report":
		return t, nil
	}
	return "", badParam(fmt.Sprintf("--expects must be one of sha, verdict, decision, report, not '%s'", raw), "")
}

// begin is task.begin (actions.task_begin): the founder's own message to the caller is approval enough, so the caller opens a task for
// itself and starts it. What stands in for the approval is the record: why on the event, and the message linked to the task.
func begin(c *rpc.Conn, p map[string]any) (any, error) {
	title, err := text(p, "title")
	if err != nil {
		return nil, err
	}
	why, err := text(p, "why")
	if err != nil {
		return nil, err
	}
	if p["body"] != nil {
		return nil, badParam("body is not served yet: a task's words are its title and the founder's message it links", "body")
	}
	chat, err := idParam(p, "message")
	if err != nil {
		return nil, err
	}
	prio := int64(db.DefaultPriority)
	if p["priority"] != nil {
		if prio, err = parsePriority(p["priority"]); err != nil {
			return nil, err
		}
	}
	expects := "report"
	if p["expects"] != nil {
		if expects, err = parseExpects(p["expects"]); err != nil {
			return nil, err
		}
	}
	me := c.Caller.Actor()
	clean := strings.TrimSpace(textsafe.Oneline(title))
	if clean == "" {
		return nil, badParam("a task needs a title", "title")
	}
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if me == human {
			return nil, refusal("a task is begun by an agent, never in the founder's name")
		}
		said := strings.TrimSpace(textsafe.Oneline(why))
		if said == "" {
			return nil, refusal("say what led to this (--why): the record is the approval")
		}
		req, err := db.GetRequest(tx, chat)
		if err != nil {
			return nil, err
		}
		if req == nil {
			return nil, refusal(fmt.Sprintf("no request #%d", chat))
		}
		if req.Requester != human || req.Target != me {
			return nil, refusal(fmt.Sprintf("request #%d is not a message from the founder to %s: only that lets you start a task without approval", chat, me))
		}
		used, err := db.TasksForRequest(tx, chat)
		if err != nil {
			return nil, err
		}
		if len(used) > 0 {
			return nil, refusal(fmt.Sprintf("request #%d already opened T%d", chat, used[0].ID))
		}
		tid, err := db.NewTask(tx, clean, me, me, 0, prio, expects, 0)
		if err != nil {
			return nil, err
		}
		if err := db.LinkRequest(tx, tid, chat); err != nil {
			return nil, err
		}
		held, has, err := db.CurrentTask(tx, me)
		if err != nil {
			return nil, err
		}
		if !has {
			if err := db.SetCurrentTask(tx, me, tid, true); err != nil {
				return nil, err
			}
		}
		if err := addEvent(tx, me, "task_begin", kv{"task_id", tid}, kv{"chat_request", chat}, kv{"why", said}); err != nil {
			return nil, err
		}
		msg := fmt.Sprintf("task T%d begun from founder message #%d", tid, chat)
		if !has {
			msg += " and is now your current task"
		} else {
			msg += fmt.Sprintf("; T%d is still current - `hl task start T%d` when you switch", held, tid)
		}
		return accepted(msg, map[string]any{"task": tid}), nil
	})
}
