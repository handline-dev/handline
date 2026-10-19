package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// reopenMethod is task.reopen: a done task sent back to doing (actions.reopen_task) by anyone, with a one-line reason its opener and
// assignee are told. A discarded task comes back as a draft: its requests were closed with it, so it is queued again to be sent.
func reopenMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		note := ""
		if v, ok := p["note"]; ok && v != nil {
			if note, ok = v.(string); !ok {
				return nil, badParam("note is text", "note")
			}
		}
		by := c.Caller.Actor()
		var notes []told
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			t, err := getTask(tx, tid)
			if err != nil {
				return nil, err
			}
			if t == nil {
				return nil, refusal(fmt.Sprintf("no task T%d", tid))
			}
			discarded := t.DiscardedBy.Valid && t.DiscardedBy.String != ""
			if !t.done() && !discarded {
				return nil, refusal(fmt.Sprintf("task T%d is not done - nothing to reopen", tid))
			}
			said := strings.TrimSpace(textsafe.Oneline(note))
			how := ""
			if discarded {
				how = " as a draft"
			}
			msg := fmt.Sprintf("task T%d reopened%s by %s", tid, how, by)
			if said != "" {
				msg += ": " + said
			}
			if discarded {
				res, err := tx.Exec("UPDATE tasks SET discarded_by = NULL, discarded_at = NULL, discard_reason = NULL, queued_by = NULL "+
					"WHERE id = ? AND discarded_by IS NOT NULL", tid)
				if err != nil {
					return nil, err
				}
				if n, _ := res.RowsAffected(); n == 0 {
					return nil, refusal(fmt.Sprintf("task T%d was reopened by someone else just now", tid))
				}
				msg += fmt.Sprintf(" - `hl task queue T%d` sends it again", tid)
				if err := addEvent(tx, by, "reopen_task", kv{"task_id", tid}, kv{"reason", said}, kv{"was", "discarded"}); err != nil {
					return nil, err
				}
				if notes, err = tellTask(tx, home, by, msg, t.OpenedBy, t.Owner); err != nil {
					return nil, err
				}
				return accepted(msg, nil), nil
			}
			if err := refreshSent(tx, tid); err != nil { // done cleared it; a request still out on it sends it again
				return nil, err
			}
			t, err = getTask(tx, tid)
			if err != nil {
				return nil, err
			}
			doing, err := isDoing(tx, t)
			if err != nil {
				return nil, err
			}
			q := "UPDATE tasks SET done_evidence = NULL, done_at = NULL, done_via = NULL, timeout_at = NULL, timeout_left = NULL WHERE id = ? AND done_evidence IS NOT NULL"
			args := []any{tid}
			if doing { // a doing task's clock restarts from now
				q = "UPDATE tasks SET done_evidence = NULL, done_at = NULL, done_via = NULL, " +
					"timeout_left = NULL, timeout_at = CASE WHEN timeout_s IS NOT NULL THEN ? + timeout_s ELSE NULL END WHERE id = ? AND done_evidence IS NOT NULL"
				args = []any{time.Now().Unix(), tid}
			}
			res, err := tx.Exec(q, args...)
			if err != nil {
				return nil, err
			}
			if n, _ := res.RowsAffected(); n == 0 {
				return nil, refusal(fmt.Sprintf("task T%d was reopened by someone else just now", tid))
			}
			after, err := newestRequest(tx)
			if err != nil {
				return nil, err
			}
			if err := addEvent(tx, by, "reopen_task", kv{"task_id", tid}, kv{"reason", said}, kv{"after_request", after}); err != nil {
				return nil, err
			}
			if notes, err = tellTask(tx, home, by, msg, t.OpenedBy, t.Owner); err != nil {
				return nil, err
			}
			return accepted(msg, nil), nil
		})
		if err == nil && tell != nil {
			tell.Notified(notes)
		}
		return out, err
	}
}
