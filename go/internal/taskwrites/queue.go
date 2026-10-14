package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
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
			done      []int64
			notes     []told
			withdrawn map[int64][]Retired
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			done, notes, withdrawn = nil, nil, map[int64][]Retired{}
			var said []string
			seen := map[int64]bool{}
			for _, tid := range tids {
				if seen[tid] {
					continue
				}
				seen[tid] = true
				msg, told, closed, err := setQueued(tx, home, tid, by, queued)
				if err != nil {
					return nil, err
				}
				said, notes = append(said, msg), append(notes, told...)
				withdrawn[tid] = closed
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
				if closed := withdrawn[tid]; len(closed) > 0 {
					delivered[i] += " - " + strings.Join(tell.Released(tid, draftWhy(tid, by), closed), "; ")
				}
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

func draftWhy(tid int64, by string) string { return fmt.Sprintf("T%d made draft by %s", tid, by) }

// setQueued is _queue_state: the checks and the write, answering the message that goes in the effect, the notices of it, and the delivery
// requests a draft withdrew (nobody works a draft).
func setQueued(tx *sql.Tx, home string, tid int64, by string, queued bool) (string, []told, []Retired, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return "", nil, nil, err
	}
	if t == nil {
		return "", nil, nil, refusal(fmt.Sprintf("no task T%d", tid))
	}
	st, err := state(tx, t)
	if err != nil {
		return "", nil, nil, err
	}
	want, other := "draft", "queued"
	if queued {
		want, other = "queued", "draft"
	}
	if st == want {
		return "", nil, nil, refusal(fmt.Sprintf("task T%d is already %s", tid, want))
	}
	if st != other {
		return "", nil, nil, refusal(fmt.Sprintf("task T%d is %s - only a %s task can be made %s", tid, st, other, want))
	}
	var (
		res    sql.Result
		closed []Retired
	)
	if queued {
		res, err = tx.Exec("UPDATE tasks SET queued_by = ?, queued_at = ? WHERE id = ? AND "+openSQL+" AND queued_by IS NULL", by, time.Now().Unix(), tid)
	} else {
		if closed, err = withdrawDelivery(tx, t, by); err != nil {
			return "", nil, nil, err
		}
		res, err = tx.Exec("UPDATE tasks SET queued_by = NULL, queued_at = NULL WHERE id = ? AND "+openSQL, tid)
	}
	if err != nil {
		return "", nil, nil, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return "", nil, nil, refusal(fmt.Sprintf("task T%d changed under this call - read it again", tid))
	}
	if !queued {
		if t, err = getTask(tx, tid); err != nil {
			return "", nil, nil, err
		}
		if st, err := state(tx, t); err != nil {
			return "", nil, nil, err
		} else if st != "draft" {
			why := fmt.Sprintf("task T%d is still %s", tid, st)
			if child, err := childAsking(tx, tid); err != nil {
				return "", nil, nil, err
			} else if child != 0 {
				why += fmt.Sprintf(": its child T%d is out - make that draft first", child)
			}
			return "", nil, nil, refusal(why)
		}
	}
	after, err := newestRequest(tx)
	if err != nil {
		return "", nil, nil, err
	}
	kind, word := "draft_task", "draft"
	if queued {
		kind, word = "queue_task", "queued"
	}
	if err := addEvent(tx, by, kind, kv{"task_id", tid}, kv{"after_request", after}); err != nil {
		return "", nil, nil, err
	}
	msg := fmt.Sprintf("task T%d is %s (%s)", tid, word, by)
	notes, err := tellTask(tx, home, by, msg, t.OpenedBy, t.Owner)
	return msg, notes, closed, err
}

// withdrawDelivery closes the requests still asking t's owner to work it, as rejected, nobody told of the close (the draft notice tells the
// opener and the owner). One that has landed is refused: its owner holds the task.
func withdrawDelivery(tx *sql.Tx, t *task, by string) ([]Retired, error) {
	reqs, err := taskRequests(tx, t.ID)
	if err != nil {
		return nil, err
	}
	var closed []Retired
	for _, r := range reqs {
		if !r.asks() {
			continue
		}
		if r.State == "landed" {
			return nil, refusal(fmt.Sprintf("task T%d has landed at %s (#%d), who holds it - a draft takes back only what has not landed", t.ID, r.Target, r.ID))
		}
		ok, err := db.CloseIfQueued(tx, r.ID, "rejected", draftWhy(t.ID, by), db.CloseOpts{Announced: true, NoSettle: true})
		if err != nil {
			return nil, err
		}
		if !ok {
			return nil, refusal(fmt.Sprintf("task T%d changed under this call - read it again", t.ID))
		}
		if err := addEvent(tx, r.Requester, "closed_under", kv{"request_id", r.ID}, kv{"target", r.Target}); err != nil {
			return nil, err
		}
		closed = append(closed, Retired{RID: r.ID, Requester: r.Requester, Target: r.Target, Delivered: r.DeliveredAt.Valid})
	}
	if len(closed) > 0 {
		return closed, db.RefreshSent(tx, t.ID)
	}
	return nil, nil
}
