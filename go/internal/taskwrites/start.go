package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// start makes a task the caller's current one (actions.task_start): only its owner, only while it is open.
func start(c *rpc.Conn, p map[string]any) (any, error) {
	tid, err := taskID(p)
	if err != nil {
		return nil, err
	}
	me := c.Caller.Actor()
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		t, err := getTask(tx, tid)
		if err != nil {
			return nil, err
		}
		switch {
		case t == nil:
			return nil, refusal(fmt.Sprintf("no task T%d", tid))
		case t.Owner != me:
			return nil, refusal(fmt.Sprintf("task T%d is %s's, not yours to start", tid, t.Owner))
		case t.DiscardedBy.Valid && t.DiscardedBy.String != "":
			return nil, refusal(fmt.Sprintf("task T%d is discarded - nothing to start", tid))
		case t.done():
			return nil, refusal(fmt.Sprintf("task T%d is already done - nothing to start", tid))
		}
		var held sql.NullInt64
		if err := tx.QueryRow("SELECT current_task FROM agent_state WHERE agent = ?", me).Scan(&held); err != nil && err != sql.ErrNoRows {
			return nil, err
		}
		now := time.Now().Unix()
		// db.set_current_task: a task in hand ends the idle spell an agent-level progress note was written in.
		if _, err := tx.Exec("INSERT INTO meta(name, value, updated) VALUES(?, NULL, ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
			"progress-agent:"+me, now); err != nil {
			return nil, err
		}
		if _, err := tx.Exec("INSERT INTO agent_state(agent, state, updated, current_task) VALUES(?, 'IDLE', ?, ?) "+
			"ON CONFLICT(agent) DO UPDATE SET current_task=excluded.current_task, updated=excluded.updated", me, now, tid); err != nil {
			return nil, err
		}
		if err := promoteOwn(tx, tid, me); err != nil {
			return nil, err
		}
		var was any
		if held.Valid {
			was = held.Int64
		}
		if err := addEvent(tx, me, "task_start", kv{"task_id", tid}, kv{"was_current", was}); err != nil {
			return nil, err
		}
		back := ""
		if held.Valid && held.Int64 != tid {
			prev, err := getTask(tx, held.Int64)
			if err != nil {
				return nil, err
			}
			if prev != nil && !prev.done() && !(prev.DiscardedBy.Valid && prev.DiscardedBy.String != "") {
				back = fmt.Sprintf("; T%d goes back to queued (your `hl progress` note is kept)", held.Int64)
			}
		}
		memos, err := memoSummary(tx, tid)
		if err != nil {
			return nil, err
		}
		msg := fmt.Sprintf("T%d is now %s's current task%s", tid, me, back)
		if memos != "" {
			msg += "\n" + memos
		}
		return accepted(msg, nil), nil
	})
}

// promoteOwn moves the task's still-queued requests for `me` to the front of what is queued for them (db.promote_to_front).
func promoteOwn(tx *sql.Tx, tid int64, me string) error {
	rows, err := tx.Query("SELECT r.id FROM requests r JOIN task_requests l ON l.request_id = r.id "+
		"WHERE l.task_id = ? AND r.target = ? AND r.delivered_at IS NULL AND "+
		"(r.state = 'queued' OR (r.state = 'landed' AND r.kind = 'task' AND r.queue_order IS NOT NULL)) ORDER BY r.id", tid, me)
	if err != nil {
		return err
	}
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return err
		}
		ids = append(ids, id)
	}
	rows.Close()
	for _, id := range ids {
		var state string
		if err := tx.QueryRow("SELECT state FROM requests WHERE id = ?", id).Scan(&state); err != nil {
			return err
		}
		if state != "queued" {
			continue
		}
		var floor sql.NullFloat64
		if err := tx.QueryRow("SELECT MIN(queue_order) FROM requests WHERE target = ? AND state = 'queued' AND delivered_at IS NULL", me).Scan(&floor); err != nil {
			return err
		}
		order := float64(id)
		if floor.Valid {
			order = floor.Float64 - 1
		}
		if _, err := tx.Exec("UPDATE requests SET queue_order = ? WHERE id = ?", order, id); err != nil {
			return err
		}
	}
	return nil
}

// memoSummary is the task's memos in one short line (actions.memo_summary): author, time, body; "" when it has none.
func memoSummary(tx *sql.Tx, tid int64) (string, error) {
	rows, err := tx.Query("SELECT author, body, created_at, edited_at FROM task_memos WHERE task_id = ? ORDER BY id", tid)
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var parts []string
	for rows.Next() {
		var author, body string
		var created float64
		var edited sql.NullFloat64
		if err := rows.Scan(&author, &body, &created, &edited); err != nil {
			return "", err
		}
		at := created
		if edited.Valid && edited.Float64 != 0 {
			at = edited.Float64
		}
		line := []rune(textsafe.Oneline(body))
		if len(line) > 160 {
			line = line[:160]
		}
		parts = append(parts, fmt.Sprintf("%s %s: %s", author, time.Unix(int64(at), 0).Format("01-02 15:04"), string(line)))
	}
	if len(parts) == 0 {
		return "", rows.Err()
	}
	text := []rune("Memos - " + strings.Join(parts, " | "))
	if len(text) > 500 {
		return strings.TrimRight(string(text[:500]), " \t\n\r\v\f") + "…", rows.Err()
	}
	return string(text), rows.Err()
}
