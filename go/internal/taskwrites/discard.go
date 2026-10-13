package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// discardMethod is task.discard: anyone's call, with a one-line reason its opener, assignee and asker are told.
func discardMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		reason, err := text(p, "reason")
		if err != nil {
			return nil, err
		}
		return discard(c, p, home, tell, reason, false, false)
	}
}

// cancelMethod is task.cancel: the founder's discard, which stops the requests out on a doing task instead of refusing them.
func cancelMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		reason := "cancelled by the founder"
		if v := p["reason"]; v != nil {
			s, ok := v.(string)
			if !ok {
				return nil, badParam("reason is text", "reason")
			}
			if strings.TrimSpace(s) != "" {
				reason = s
			}
		}
		return discard(c, p, home, tell, reason, true, false)
	}
}

// withdrawMethod is task.withdraw: an opener throwing its own task away, so with no reason of its own.
func withdrawMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		return discard(c, p, home, tell, "withdrawn by whoever opened it", false, true)
	}
}

// discard throws a task away before it is done (actions.discard_task, without the founder's stop of open requests, which is
// task.cancel's): refused while a child is open, while it waits on an open blocker, or while a request is still out on it.
// What follows the commit - the says typed, the owner's next task, the closed requests' inbox files - is the Teller's.
// own is a withdraw: only the asker may, since anyone else must say why.
func discard(c *rpc.Conn, p map[string]any, home string, tell Teller, reason string, stop, own bool) (any, error) {
	tid, err := taskID(p)
	if err != nil {
		return nil, err
	}
	by := c.Caller.Actor()
	var (
		owner     string
		notes     []told
		freed     []string
		forget    []db.InboxFile
		cancelled []Retired
		sayStop   bool
	)
	out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		t, err := getTask(tx, tid)
		if err != nil {
			return nil, err
		}
		switch {
		case t == nil:
			return nil, refusal(fmt.Sprintf("no task T%d", tid))
		case t.DiscardedBy.Valid && t.DiscardedBy.String != "":
			return nil, refusal(fmt.Sprintf("task T%d is already discarded (%s)", tid, t.DiscardedBy.String))
		case t.done():
			return nil, refusal(fmt.Sprintf("task T%d is done - `hl task reopen T%d` if the result was wrong", tid, tid))
		}
		who, err := asker(tx, t)
		if err != nil {
			return nil, err
		}
		if own && by != who {
			return nil, refusal(fmt.Sprintf("task T%d was asked for by %s, so only they withdraw it - discard it with a reason instead", tid, who))
		}
		said := strings.TrimSpace(textsafe.Oneline(reason))
		if said == "" {
			return nil, refusal("discarding needs a one-line reason")
		}
		fromAbove := by == human // live children outlive a parent the founder clears: they move up
		kids, err := openChildren(tx, tid)
		if err != nil {
			return nil, err
		}
		if len(kids) > 0 && !fromAbove {
			return nil, refusal(fmt.Sprintf("task T%d still has open child tasks (%s) - deal with them first", tid, taskList(kids)))
		}
		blockers, err := taskBlockers(tx, tid)
		if err != nil {
			return nil, err
		}
		var waits []int64
		for _, b := range blockers {
			if !(fromAbove && b.Parent.Valid && b.Parent.Int64 == tid) {
				waits = append(waits, b.ID)
			}
		}
		if len(waits) > 0 && by != human {
			return nil, refusal(fmt.Sprintf("task T%d is waiting on %s - it cannot be discarded until they close", tid, taskList(waits)))
		}
		reqs, err := taskRequests(tx, tid)
		if err != nil {
			return nil, err
		}
		var live []linked
		for _, r := range reqs {
			if r.asks() {
				live = append(live, r)
			}
		}
		cancelled, sayStop = nil, false
		inHand := false
		if stop && by == human && len(live) > 0 { // the founder's stop: the open requests are cancelled instead of refusing
			cur, _, err := db.CurrentTask(tx, t.Owner)
			if err != nil {
				return nil, err
			}
			delivered := false
			for _, r := range live {
				delivered = delivered || r.DeliveredAt.Valid
			}
			inHand = cur == tid || delivered
			sayStop = inHand && t.Owner != human && !delivered
			for _, r := range live {
				if r.State != "queued" {
					continue // landed: held by its agent, told to stop below; nothing to cancel
				}
				plan, err := cancelRequest(tx, r)
				if err != nil {
					return nil, err
				}
				if plan == nil {
					return nil, refusal(fmt.Sprintf("task T%d not discarded: #%d was closed while this ran - nothing to cancel", tid, r.ID))
				}
				if plan.RID != 0 {
					cancelled = append(cancelled, *plan)
				} else {
					forget = append(forget, db.InboxFile{Target: r.Target, RID: r.ID})
				}
			}
		} else if len(live) > 0 {
			var open []string
			for _, r := range live {
				open = append(open, fmt.Sprintf("#%d (with %s)", r.ID, r.Target))
			}
			return nil, refusal(fmt.Sprintf("task T%d still has a request open: %s - withdraw or cancel it first (the founder's `hl cancel <id>`), "+
				"or its target keeps working on thrown-away work", tid, strings.Join(open, ", ")))
		}
		res, err := tx.Exec("UPDATE tasks SET discarded_by = ?, discarded_at = ?, discard_reason = ? WHERE id = ? AND "+openSQL, by, time.Now().Unix(), said, tid)
		if err != nil {
			return nil, err
		}
		if _, err := tx.Exec("UPDATE tasks SET sent_at = NULL WHERE id = ?", tid); err != nil {
			return nil, err
		}
		if n, _ := res.RowsAffected(); n == 0 {
			return nil, refusal(fmt.Sprintf("task T%d was done or discarded by someone else just now", tid))
		}
		if err := endHeld(tx, tid); err != nil {
			return nil, err
		}
		if err := addEvent(tx, by, "discard_task", kv{"task_id", tid}, kv{"reason", said}); err != nil {
			return nil, err
		}
		byWord := by
		if by == human {
			byWord = "the founder"
		}
		var also []string
		if !inHand {
			also = append(also, t.Owner)
		}
		also = append(also, who, t.OpenedBy)
		ended, gone, err := endTask(tx, home, tid, by, t, strings.TrimRight(fmt.Sprintf("T%d discarded by %s: %s", tid, byWord, said), ": "), false, also)
		if err != nil {
			return nil, err
		}
		notes, forget = ended, append(forget, gone...)
		for _, b := range blockers { // only the founder gets here with any: the wait ends with the task
			if err := removeBlock(tx, tid, b.ID); err != nil {
				return nil, err
			}
		}
		if err := reparentChildren(tx, tid); err != nil {
			return nil, err
		}
		cleared, owners, err := clearBlocksOn(tx, tid, by, "discarded")
		if err != nil {
			return nil, err
		}
		notes, freed, owner = append(notes, cleared...), owners, t.Owner
		return accepted(fmt.Sprintf("task T%d discarded by %s: %s", tid, by, said), nil), nil
	})
	if err == nil && owner != "" && tell != nil {
		tell.Cancelled(tid, by, owner, sayStop, cancelled)
		tell.Discarded(owner, notes, freed, forget)
	}
	return out, err
}

// endHeld takes the task out of its owners' queues: the landed delivery that asked for it is history from now on.
func endHeld(tx *sql.Tx, tid int64) error {
	_, err := tx.Exec("UPDATE requests SET queue_order = NULL WHERE state = 'landed' AND kind = 'task' AND queue_order IS NOT NULL "+
		"AND id IN (SELECT request_id FROM task_requests WHERE task_id = ?)", tid)
	return err
}

func taskList(ids []int64) string {
	parts := make([]string, len(ids))
	for i, id := range ids {
		parts[i] = fmt.Sprintf("T%d", id)
	}
	return strings.Join(parts, ", ")
}

// cancelRequest is the write of the founder's cancel (team/ask.py cancel) for one queued request: the founder's own undelivered one is
// withdrawn (plan.RID 0), any other closes as rejected under the founder's words. nil when it was closed while this ran.
func cancelRequest(tx *sql.Tx, r linked) (*Retired, error) {
	if r.Requester == human && !r.DeliveredAt.Valid {
		ok, err := db.RetireIfUndelivered(tx, r.ID, "withdrawn", "")
		if err != nil || !ok {
			return nil, err
		}
		if err := addEvent(tx, human, "withdraw", kv{"request_id", r.ID}, kv{"target", r.Target}, kv{"reason", nil}); err != nil {
			return nil, err
		}
		return &Retired{}, nil
	}
	notify := r.Requester != human
	ok, err := db.CloseIfQueued(tx, r.ID, "rejected", CancelVerdict, db.CloseOpts{Announced: !notify})
	if err != nil || !ok {
		return nil, err
	}
	if err := addEvent(tx, r.Requester, "closed_under", kv{"request_id", r.ID}, kv{"target", r.Target}); err != nil {
		return nil, err
	}
	if err := addEvent(tx, human, "cancel", kv{"request_id", r.ID}, kv{"target", r.Target}, kv{"requester", r.Requester}, kv{"delivered", r.DeliveredAt.Valid}); err != nil {
		return nil, err
	}
	return &Retired{RID: r.ID, Requester: r.Requester, Target: r.Target, Delivered: r.DeliveredAt.Valid, Notify: notify}, nil
}
