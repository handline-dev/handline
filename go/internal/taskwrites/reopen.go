package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// reopen sends a done task back to doing (actions.reopen_task): the asker's or the founder's call, with a one-line reason.
func reopen(c *rpc.Conn, p map[string]any) (any, error) {
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
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		t, err := getTask(tx, tid)
		if err != nil {
			return nil, err
		}
		if t == nil {
			return nil, refusal(fmt.Sprintf("no task T%d", tid))
		}
		if !t.done() {
			return nil, refusal(fmt.Sprintf("task T%d is not done - nothing to reopen", tid))
		}
		who, err := asker(tx, t)
		if err != nil {
			return nil, err
		}
		if by != who && by != human {
			return nil, refusal(fmt.Sprintf("task T%d can only be reopened by %s - whoever asked for it - or the founder", tid, who))
		}
		said := strings.TrimSpace(textsafe.Oneline(note))
		if said == "" {
			return nil, refusal("reopening needs a one-line reason")
		}
		msg := fmt.Sprintf("task T%d reopened by %s: %s", tid, by, said)
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
		q := "UPDATE tasks SET done_evidence = NULL, done_at = NULL, done_via = NULL, timeout_at = NULL WHERE id = ? AND done_evidence IS NOT NULL"
		args := []any{tid}
		if doing { // a doing task's clock restarts from now
			q = "UPDATE tasks SET done_evidence = NULL, done_at = NULL, done_via = NULL, " +
				"timeout_at = CASE WHEN timeout_s IS NOT NULL THEN ? + timeout_s ELSE NULL END WHERE id = ? AND done_evidence IS NOT NULL"
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
		if err := addEvent(tx, by, "reopen_task", map[string]any{"task_id": tid, "reason": said, "after_request": after}); err != nil {
			return nil, err
		}
		return accepted(msg, nil), nil
	})
}
