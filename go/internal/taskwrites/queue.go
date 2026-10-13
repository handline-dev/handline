package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// queueMethod is task.queue (approve drafts to run) and task.draft (send queued ones back): anyone's call, all the named tasks changing
// or none (actions.set_queued_all), each task's opener and assignee told.
func queueMethod(home string, tell Teller, queued bool) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tids, err := taskIDs(p, "tasks")
		if err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		var (
			done  []int64
			notes []told
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			done, notes = nil, nil
			var said []string
			seen := map[int64]bool{}
			for _, tid := range tids {
				if seen[tid] {
					continue
				}
				seen[tid] = true
				msg, told, err := setQueued(tx, home, tid, by, queued)
				if err != nil {
					return nil, err
				}
				said, notes = append(said, msg), append(notes, told...)
				done = append(done, tid)
			}
			return accepted(strings.Join(said, "\n"), nil), nil
		})
		if err == nil && tell != nil && len(done) > 0 {
			// Queuing also delivers: the task goes out now if its owner is free. The reply says what happened to each; a replay of this call
			// is answered with what was kept, which says only that the tasks are queued.
			delivered := make([]string, len(done))
			for i, tid := range done {
				delivered[i] = tell.QueueDelivered(tid, by, queued)
			}
			out["said"] = strings.Join(delivered, "\n")
			tell.Notified(notes)
		}
		return out, err
	}
}

func taskIDs(p map[string]any, key string) ([]int64, error) {
	list, ok := p[key].([]any)
	if !ok || len(list) == 0 {
		return nil, badParam(key+" is a list of task numbers", key)
	}
	out := make([]int64, len(list))
	for i, v := range list {
		n, ok := rpc.AsInt(v)
		if !ok {
			return nil, badParam(key+" is a list of task numbers", key)
		}
		out[i] = n
	}
	return out, nil
}

// setQueued is _queue_state: the checks and the write, answering the message that goes in the effect and the notices of it.
func setQueued(tx *sql.Tx, home string, tid int64, by string, queued bool) (string, []told, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return "", nil, err
	}
	if t == nil {
		return "", nil, refusal(fmt.Sprintf("no task T%d", tid))
	}
	st, err := state(tx, t)
	if err != nil {
		return "", nil, err
	}
	want, other := "draft", "queued"
	if queued {
		want, other = "queued", "draft"
	}
	if st == want {
		return "", nil, refusal(fmt.Sprintf("task T%d is already %s", tid, want))
	}
	if st != other {
		return "", nil, refusal(fmt.Sprintf("task T%d is %s - only a %s task can be made %s", tid, st, other, want))
	}
	var res sql.Result
	if queued {
		res, err = tx.Exec("UPDATE tasks SET queued_by = ?, queued_at = ? WHERE id = ? AND "+openSQL+" AND queued_by IS NULL", by, time.Now().Unix(), tid)
	} else {
		res, err = tx.Exec("UPDATE tasks SET queued_by = NULL, queued_at = NULL WHERE id = ? AND "+openSQL+" AND queued_by IS NOT NULL", tid)
	}
	if err != nil {
		return "", nil, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return "", nil, refusal(fmt.Sprintf("task T%d changed under this call - read it again", tid))
	}
	after, err := newestRequest(tx)
	if err != nil {
		return "", nil, err
	}
	kind, word := "draft_task", "draft"
	if queued {
		kind, word = "queue_task", "queued"
	}
	if err := addEvent(tx, by, kind, kv{"task_id", tid}, kv{"after_request", after}); err != nil {
		return "", nil, err
	}
	msg := fmt.Sprintf("task T%d is %s (%s)", tid, word, by)
	notes, err := tellTask(tx, home, t, by, msg)
	return msg, notes, err
}
