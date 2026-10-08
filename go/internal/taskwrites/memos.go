// Package taskwrites serves the write methods of the task board (handline/daemon/rpc_tasks.py, rpc_taskverbs.py and the
// actions behind them): each makes its change and records its result in one transaction (rpc.Conn.Effect).
package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

const human = "human"

// CancelVerdict is what a request the founder cancels says it was closed under.
const CancelVerdict = "withdrawn by the founder"

// Register adds the task writes to the server's table.
// Teller is what follows a write once it has committed: the typed says and the deliveries the Python write did after its commit
// (the supervisor's telling layer). A nil Teller tells nothing.
type Teller interface {
	// MemoChanged tells the holder of a doing task that a memo was added, edited or deleted (what), with the memo's text.
	MemoChanged(tid int64, by, what, text string)
	// QueueDelivered sends a queued task to its free owner, or raises the founder's card, and answers what happened.
	QueueDelivered(tid int64, by string, queued bool) string
	// Discarded types the says a discard wrote, moves the owner on to its next task, advances the owners it freed, and removes the inbox
	// files of the requests it closed.
	Discarded(owner string, told []db.Notice, freed []string, forget []db.InboxFile)
	// Released stops the owner on the held requests a release ended, tells the requesters of the queued ones it closed (why is the reason on
	// them), moves the queues up, and answers a phrase for each ("#12 ended; w told to stop").
	Released(tid int64, why string, retired []Retired) []string
	// Cancelled is what the founder's cancel leaves before the discard's own telling: each request it closed told like any cancel, and the
	// owner typed a stop when it held the task without a delivery to stop it (stop).
	Cancelled(tid int64, by, owner string, stop bool, closed []Retired)
	// Done is what a done task leaves after the commit: the inbox files of the requests it closed go, the owner moves on to its next task, and
	// (finished) the tasks it was the last blocker of are released; the says it wrote are typed. finished is false when only a leftover request
	// of an already done task was settled.
	Done(tid int64, by, owner string, told []db.Notice, forget []db.InboxFile, finished bool)
}

func Register(s *rpc.Server, home string, tell Teller) {
	s.Register("task.discard", rpc.Method{Role: "agent", Write: true, Handler: discardMethod(home, tell)})
	s.Register("task.withdraw", rpc.Method{Role: "agent", Write: true, Handler: withdrawMethod(home, tell)})
	s.Register("task.queue", rpc.Method{Role: "agent", Write: true, Handler: queueMethod(home, tell, true)})
	s.Register("task.draft", rpc.Method{Role: "agent", Write: true, Handler: queueMethod(home, tell, false)})
	s.Register("task.start", rpc.Method{Role: "agent", Write: true, Handler: start})
	s.Register("task.reopen", rpc.Method{Role: "agent", Write: true, Handler: reopen})
	s.Register("task.done", rpc.Method{Role: "agent", Write: true, Handler: doneMethod(home, tell)})
	s.Register("task.cancel", rpc.Method{Role: "member", Write: true, Handler: cancelMethod(home, tell)})
	s.Register("task.release", rpc.Method{Role: "agent", Write: true, Handler: releaseMethod(tell)})
	s.Register("task.merge", rpc.Method{Role: "agent", Write: true, Handler: merge})
	s.Register("task.memo.add", rpc.Method{Role: "agent", Write: true, Handler: memoAdd(tell)})
	s.Register("task.memo.edit", rpc.Method{Role: "agent", Write: true, Handler: memoEdit(tell)})
	s.Register("task.memo.delete", rpc.Method{Role: "agent", Write: true, Handler: memoDelete(tell)})
}

func badParam(message, field string) *rpc.Error {
	return rpc.BadRequestError(message, field, rpc.InvalidParams)
}

func taskID(p map[string]any) (int64, error) {
	n, ok := rpc.AsInt(p["task"])
	if !ok {
		return 0, badParam("task is a task number", "task")
	}
	return n, nil
}

func memoID(p map[string]any) (int64, error) {
	n, ok := rpc.AsInt(p["memo"])
	if !ok {
		return 0, badParam("memo is a memo number", "memo")
	}
	return n, nil
}

func text(p map[string]any, key string) (string, error) {
	s, ok := p[key].(string)
	if !ok || strings.TrimSpace(s) == "" {
		return "", badParam(key+" is text and not empty", key)
	}
	return s, nil
}

func accepted(said string, more map[string]any) map[string]any {
	out := map[string]any{"accepted": true, "said": said}
	for k, v := range more {
		out[k] = v
	}
	return out
}

// refusal is an action's "no": a missing task is not_found, anything else refused.
func refusal(msg string) error {
	if strings.HasPrefix(msg, "no task T") {
		e := rpc.NotFoundError(msg)
		e.Data = map[string]any{"what": "task"}
		return e
	}
	return rpc.Refused(msg, "", "")
}

func hasTask(tx *sql.Tx, tid int64) (bool, error) {
	var n int
	err := tx.QueryRow("SELECT COUNT(*) FROM tasks WHERE id = ?", tid).Scan(&n)
	return n > 0, err
}

// memoOf is the memo `mid` of task `tid`, or not_found.
func memoOf(tx *sql.Tx, tid, mid int64) (author, body string, err error) {
	var of int64
	switch err = tx.QueryRow("SELECT task_id, author, body FROM task_memos WHERE id = ?", mid).Scan(&of, &author, &body); {
	case err == sql.ErrNoRows || (err == nil && of != tid):
		e := rpc.NotFoundError(fmt.Sprintf("T%d has no memo %d", tid, mid))
		e.Data = map[string]any{"what": "memo", "name": fmt.Sprint(mid)}
		return "", "", e
	}
	return author, body, err
}

// guard: a memo is anyone's to add; only its author or the founder edits or deletes it.
func guard(by, author string, mid int64) error {
	if by == human || by == author {
		return nil
	}
	return refusal(fmt.Sprintf("memo %d is %s's: only its author or the founder edits or deletes it", mid, author))
}

// The holder of a doing task is not told of a memo change here: the telling is a typed say, the delivery layer's.

func memoAdd(tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		body, err := text(p, "text")
		if err != nil {
			return nil, err
		}
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			if ok, err := hasTask(tx, tid); err != nil || !ok {
				if err != nil {
					return nil, err
				}
				return nil, refusal(fmt.Sprintf("no task T%d", tid))
			}
			res, err := tx.Exec("INSERT INTO task_memos(task_id, author, body, created_at) VALUES (?, ?, ?, ?)", tid, c.Caller.Actor(), body,
				float64(time.Now().UnixNano())/1e9)
			if err != nil {
				return nil, err
			}
			mid, _ := res.LastInsertId()
			return accepted(fmt.Sprintf("memo %d added to T%d", mid, tid), map[string]any{"memo": mid}), nil
		})
		if err == nil && tell != nil {
			tell.MemoChanged(tid, c.Caller.Actor(), "added", body)
		}
		return out, err
	}
}

func memoEdit(tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		body, err := text(p, "text")
		if err != nil {
			return nil, err
		}
		mid, err := memoID(p)
		if err != nil {
			return nil, err
		}
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			if ok, err := hasTask(tx, tid); err != nil || !ok {
				if err != nil {
					return nil, err
				}
				return nil, refusal(fmt.Sprintf("no task T%d", tid))
			}
			author, was, err := memoOf(tx, tid, mid)
			if err != nil {
				return nil, err
			}
			if err := guard(c.Caller.Actor(), author, mid); err != nil {
				return nil, err
			}
			if body == was {
				return nil, refusal(fmt.Sprintf("memo %d already reads that way - nothing to edit", mid))
			}
			if _, err := tx.Exec("UPDATE task_memos SET body = ?, edited_at = ? WHERE id = ?", body, time.Now().Unix(), mid); err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("memo %d on T%d edited", mid, tid), nil), nil
		})
		if err == nil && tell != nil {
			tell.MemoChanged(tid, c.Caller.Actor(), "edited", body)
		}
		return out, err
	}
}

func memoDelete(tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		mid, err := memoID(p)
		if err != nil {
			return nil, err
		}
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			if ok, err := hasTask(tx, tid); err != nil || !ok {
				if err != nil {
					return nil, err
				}
				return nil, refusal(fmt.Sprintf("no task T%d", tid))
			}
			author, _, err := memoOf(tx, tid, mid)
			if err != nil {
				return nil, err
			}
			if err := guard(c.Caller.Actor(), author, mid); err != nil {
				return nil, err
			}
			if _, err := tx.Exec("DELETE FROM task_memos WHERE id = ?", mid); err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("memo %d deleted from T%d", mid, tid), nil), nil
		})
		if err == nil && tell != nil {
			tell.MemoChanged(tid, c.Caller.Actor(), "deleted", "")
		}
		return out, err
	}
}
