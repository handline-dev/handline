// Package taskwrites serves the write methods of the task board (handline/daemon/rpc_tasks.py, rpc_taskverbs.py and the
// actions behind them): each makes its change and records its result in one transaction (rpc.Conn.Effect).
package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/attachments"
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
	// MemoChanged tells whom a memo concerns (the holder of a doing task, who the task answers to, the agents it names with @) that it was
	// added, edited or deleted (what), with the memo's text: at once when now, else a reader mid-turn hears at the end of its turn.
	MemoChanged(tid, mid int64, by, what, text string, now bool)
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
	// TaskAsk routes the request that delivers a task to its owner, in the transaction of the write that opens or moves it: the request row (a
	// refusal's denial row too, which stands), then within once the ask is allowed. The ask's inbox file and telling wait for the commit
	// (Asked.After).
	TaskAsk(tx *sql.Tx, in AskReq) (*Asked, error)
	// TaskSay sends the founder's question about a task as a say to `to`, typed at once, in the transaction that links it to the task.
	TaskSay(tx *sql.Tx, to, body string, within func(rid int64, note string) error) (*Asked, error)
	// HandoffRetire closes a request of the old owner's quietly (done, nobody told, the decision task it is the card of not settled), in the
	// transaction of the move. told, nil when the request was already closed, runs after the commit.
	HandoffRetire(tx *sql.Tx, rid int64, verdict string) (told func(), err error)
	// HandedOver is what a committed handoff leaves: the requests it closed told, the old owner moved on, the new owner typed the task. It
	// answers the lines the reply adds.
	HandedOver(h Handover) string
	// Edited is what a committed task.edit leaves: a changed body told to the holder of a doing task, a task that changed hands sent on to its new
	// owner, an owner whose last blocker cleared or whose current task got one moved on.
	Edited(ed Edited)
	// Notified delivers the notices a write left for the people a task's change concerns (tellTask).
	Notified(told []db.Notice)
	// MemoTellSent sends request rid, a comment's tell that was held for its reader's digest, to the reader now in a digest of its own.
	MemoTellSent(rid int64)
}

func Register(s *rpc.Server, home string, tell Teller, decide CardDecider) {
	s.Register("task.discard", rpc.Method{Role: "agent", Write: true, Handler: discardMethod(home, tell)})
	s.Register("task.withdraw", rpc.Method{Role: "agent", Write: true, Handler: withdrawMethod(home, tell)})
	s.Register("task.queue", rpc.Method{Role: "agent", Write: true, Handler: queueMethod(home, tell, true)})
	s.Register("task.draft", rpc.Method{Role: "agent", Write: true, Handler: queueMethod(home, tell, false)})
	s.Register("task.progress", rpc.Method{Role: "agent", Write: true, Handler: progress})
	s.Register("progress.get", rpc.Method{Role: "agent", Handler: progressGet})
	s.Register("task.begin", rpc.Method{Role: "agent", Write: true, Handler: func(c *rpc.Conn, p map[string]any) (any, error) { return begin(c, p, home) }})
	s.Register("task.handoff", rpc.Method{Role: "agent", Write: true, Handler: handoffMethod(home, tell)})
	s.Register("task.new", rpc.Method{Role: "agent", Write: true, Handler: newMethod(home, tell)})
	s.Register("task.edit", rpc.Method{Role: "agent", Write: true, Handler: editMethod(home, tell)})
	s.Register("task.ask", rpc.Method{Role: "member", Write: true, Handler: askMethod(home, tell)})
	s.Register("task.start", rpc.Method{Role: "agent", Write: true, Handler: start})
	s.Register("task.reopen", rpc.Method{Role: "agent", Write: true, Handler: reopenMethod(home, tell)})
	s.Register("task.done", rpc.Method{Role: "agent", Write: true, Handler: doneMethod(home, tell, decide)})
	s.Register("task.cancel", rpc.Method{Role: "member", Write: true, Handler: cancelMethod(home, tell)})
	s.Register("task.request", rpc.Method{Role: "agent", Write: true, Handler: requestMethod(home)})
	s.Register("task.none", rpc.Method{Role: "agent", Write: true, Handler: noneMethod(home)})
	s.Register("task.release", rpc.Method{Role: "agent", Write: true, Handler: releaseMethod(home, tell)})
	s.Register("task.wait", rpc.Method{Role: "agent", Write: true, Handler: waitMethod(home, tell)})
	s.Register("task.merge", rpc.Method{Role: "agent", Write: true, Handler: mergeMethod(home, tell)})
	for _, verb := range []string{"task.comment.", "task.memo."} { // task.memo.* is the old name
		s.Register(verb+"add", rpc.Method{Role: "agent", Write: true, Handler: memoAdd(home, tell)})
		s.Register(verb+"edit", rpc.Method{Role: "agent", Write: true, Handler: memoEdit(home, tell)})
		s.Register(verb+"delete", rpc.Method{Role: "agent", Write: true, Handler: memoDelete(tell)})
	}
	s.Register("task.comment.send_now", rpc.Method{Role: "member", Write: true, Handler: memoSendNow(tell)})
	s.Register("task.view.save", rpc.Method{Role: "member", Write: true, Handler: viewSave})
	s.Register("task.view.delete", rpc.Method{Role: "member", Write: true, Handler: viewDelete})
}

func claimImages(tx *sql.Tx, home, by, text, was, field string) error {
	return attachments.New(home).ClaimText(tx, text, was, field, by != human)
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

func memoAdd(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		body, err := text(p, "text")
		if err != nil {
			return nil, err
		}
		now, err := optionalBool(p, "now")
		if err != nil {
			return nil, err
		}
		reply, hasReply, err := optionalTask(p, "reply")
		if err != nil {
			return nil, err
		}
		paths, err := attachments.FilePaths(p)
		if err != nil {
			return nil, err
		}
		store := attachments.New(home)
		var files []attachments.Info
		var added int64
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			if ok, err := hasTask(tx, tid); err != nil || !ok {
				if err != nil {
					return nil, err
				}
				return nil, refusal(fmt.Sprintf("no task T%d", tid))
			}
			if err := claimImages(tx, home, c.Caller.Actor(), body, "", "text"); err != nil {
				return nil, err
			}
			if hasReply {
				if _, _, err := memoOf(tx, tid, reply); err != nil {
					return nil, err
				}
			}
			mid, err := db.AddMemo(tx, tid, c.Caller.Actor(), body)
			if err != nil {
				return nil, err
			}
			added = mid
			if hasReply {
				if _, err := tx.Exec("UPDATE task_memos SET reply_to = ? WHERE id = ?", reply, mid); err != nil {
					return nil, err
				}
			}
			if err := noteRequestComment(tx, tid, c.Caller.Actor()); err != nil {
				return nil, err
			}
			if c.Caller.Actor() == human {
				err = answerMemoAsks(tx, tid)
			} else {
				err = syncMemoAsk(tx, tid, mid, c.Caller.Actor(), body)
			}
			if err != nil {
				return nil, err
			}
			if files, err = store.AddFiles(tx, home, c.Caller.Actor(), c.Caller.Actor() == human, paths); err != nil {
				return nil, err
			}
			if err := store.AttachMemo(tx, mid, files); err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("memo %d added to T%d", mid, tid), map[string]any{"memo": mid}), nil
		})
		if err == nil && tell != nil {
			told := body
			if lines := store.FileLines(files); lines != "" {
				told += "\n" + lines
			}
			tell.MemoChanged(tid, added, c.Caller.Actor(), "added", told, now)
		}
		return out, err
	}
}

func memoEdit(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
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
			if err := claimImages(tx, home, c.Caller.Actor(), body, was, "text"); err != nil {
				return nil, err
			}
			if err := db.EditMemo(tx, mid, c.Caller.Actor(), body); err != nil {
				return nil, err
			}
			if err := syncMemoAsk(tx, tid, mid, author, body); err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("memo %d on T%d edited", mid, tid), nil), nil
		})
		if err == nil && tell != nil {
			tell.MemoChanged(tid, mid, c.Caller.Actor(), "edited", body, false)
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
			if err := db.DeleteMemo(tx, mid, c.Caller.Actor()); err != nil {
				return nil, err
			}
			if err := db.CloseMemoAsks(tx, "withdrawn", "memo_id = ?", mid); err != nil {
				return nil, err
			}
			if err := attachments.UnlinkMemo(tx, mid); err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("memo %d deleted from T%d", mid, tid), nil), nil
		})
		if err == nil && tell != nil {
			tell.MemoChanged(tid, mid, c.Caller.Actor(), "deleted", "", false)
		}
		return out, err
	}
}

// memoSendNow is task.comment.send_now: the tell of the founder's comment memo to one reader that waits for the reader's digest goes now.
func memoSendNow(tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		mid, err := memoID(p)
		if err != nil {
			return nil, err
		}
		agent, err := text(p, "agent")
		if err != nil {
			return nil, err
		}
		var rid int64
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			err := tx.QueryRow("SELECT r.id FROM memo_tells t JOIN requests r ON r.id = t.request_id "+
				"WHERE t.memo_id = ? AND t.reader = ? AND r.notice = 2 AND r.read_at IS NULL AND r.delivered_at IS NULL",
				mid, agent).Scan(&rid)
			if err == sql.ErrNoRows {
				return nil, refusal(fmt.Sprintf("memo %d is not held for %s", mid, agent))
			}
			if err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("memo %d goes to %s now", mid, agent), nil), nil
		})
		if err == nil && tell != nil {
			tell.MemoTellSent(rid)
		}
		return out, err
	}
}
