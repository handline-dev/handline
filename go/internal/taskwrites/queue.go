package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

// queueMethod is task.queue (approve drafts to run) and task.draft (send queued ones back): the founder's or the owner's
// manager's call, all the named tasks changing or none (actions.set_queued_all).
func queueMethod(home string, tell Teller, queued bool) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tids, err := taskIDs(p, "tasks")
		if err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		var done []int64
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			var said []string
			seen := map[int64]bool{}
			for _, tid := range tids {
				if seen[tid] {
					continue
				}
				seen[tid] = true
				msg, err := setQueued(tx, home, tid, by, queued)
				if err != nil {
					return nil, err
				}
				said = append(said, msg)
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

// setQueued is _queue_state: the checks and the write, answering the message that goes in the effect.
func setQueued(tx *sql.Tx, home string, tid int64, by string, queued bool) (string, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return "", err
	}
	if t == nil {
		return "", refusal(fmt.Sprintf("no task T%d", tid))
	}
	if by != human {
		var boss string
		if tm, err := team.LoadTeam(home); err == nil {
			boss = tm.SupervisorOf(t.Owner)
		}
		if by != boss {
			return "", refusal(fmt.Sprintf("task T%d can only be queued or drafted by the founder or %s's manager", tid, t.Owner))
		}
	}
	st, err := state(tx, t)
	if err != nil {
		return "", err
	}
	want, other := "draft", "queued"
	if queued {
		want, other = "queued", "draft"
	}
	if st == want {
		return "", refusal(fmt.Sprintf("task T%d is already %s", tid, want))
	}
	if st != other {
		return "", refusal(fmt.Sprintf("task T%d is %s - only a %s task can be made %s", tid, st, other, want))
	}
	var res sql.Result
	if queued {
		res, err = tx.Exec("UPDATE tasks SET queued_by = ?, queued_at = ? WHERE id = ? AND "+openSQL+" AND queued_by IS NULL", by, time.Now().Unix(), tid)
	} else {
		res, err = tx.Exec("UPDATE tasks SET queued_by = NULL, queued_at = NULL WHERE id = ? AND "+openSQL+" AND queued_by IS NOT NULL", tid)
	}
	if err != nil {
		return "", err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return "", refusal(fmt.Sprintf("task T%d changed under this call - read it again", tid))
	}
	after, err := newestRequest(tx)
	if err != nil {
		return "", err
	}
	kind, word := "draft_task", "draft"
	if queued {
		kind, word = "queue_task", "queued"
	}
	if err := addEvent(tx, by, kind, kv{"task_id", tid}, kv{"after_request", after}); err != nil {
		return "", err
	}
	return fmt.Sprintf("task T%d is %s (%s)", tid, word, by), nil
}
