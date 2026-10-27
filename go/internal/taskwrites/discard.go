package taskwrites

import (
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// discardMethod is task.discard: anyone's call, with a one-line reason its opener, assignee and asker are told. tasks in place of task is
// a batch, one event and one chat card; reasons gives a task its own reason over reason.
func discardMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		if _, batch := p["tasks"]; batch {
			return discardBatch(c, p, home, tell)
		}
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

// allLanded is whether every open request on a task has landed in its agent's session (none is still waiting to be typed); false for none.
func allLanded(live []linked) bool {
	for _, r := range live {
		if !r.LandedAt.Valid && r.State != "landed" {
			return false
		}
	}
	return len(live) > 0
}

// discard throws one task away (discardOne) and does its telling once that commits.
func discard(c *rpc.Conn, p map[string]any, home string, tell Teller, reason string, stop, own bool) (any, error) {
	tid, err := taskID(p)
	if err != nil {
		return nil, err
	}
	by := c.Caller.Actor()
	var d *discarded
	out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		var err error
		if d, err = discardOne(tx, home, by, tid, reason, "", stop, own); err != nil {
			return nil, err
		}
		return accepted(d.said, nil), nil
	})
	if err == nil {
		d.tell(tell, by)
	}
	return out, err
}

// discardBatch is task.discard of several tasks in one call: all of them or none, with one discard_batch event listing each task, its title,
// its reason and the requests it withdrew, shown as one card in its author's chat.
func discardBatch(c *rpc.Conn, p map[string]any, home string, tell Teller) (any, error) {
	tids, err := taskIDs(p, "tasks")
	if err != nil {
		return nil, err
	}
	reason, _ := p["reason"].(string)
	each := map[int64]string{}
	if v, ok := p["reasons"]; ok && v != nil {
		m, ok := v.(map[string]any)
		if !ok {
			return nil, badParam("reasons is an object of task number to reason", "reasons")
		}
		for k, r := range m {
			tid, err := strconv.ParseInt(strings.TrimPrefix(k, "T"), 10, 64)
			why, isText := r.(string)
			if err != nil || !isText {
				return nil, badParam("reasons is an object of task number to reason", "reasons")
			}
			each[tid] = why
		}
	}
	by := c.Caller.Actor()
	batch := newBatchID()
	var done []*discarded
	out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		done = nil
		var cards []any
		var lines []string
		seen := map[int64]bool{}
		for _, tid := range tids {
			if seen[tid] {
				continue
			}
			seen[tid] = true
			why := reason
			if r, ok := each[tid]; ok {
				why = r
			}
			d, err := discardOne(tx, home, by, tid, why, batch, false, false)
			if err != nil {
				return nil, err
			}
			done = append(done, d)
			cards = append(cards, rpc.Pairs{{K: "task_id", V: tid}, {K: "title", V: d.title}, {K: "reason", V: d.reason}, {K: "withdrew", V: d.withdrew}})
			lines = append(lines, d.said)
		}
		if err := addEvent(tx, by, "discard_batch", kv{"batch", batch}, kv{"tasks", cards}); err != nil {
			return nil, err
		}
		return accepted(strings.Join(lines, "\n"), map[string]any{"batch": batch}), nil
	})
	if err == nil {
		for _, d := range done {
			d.tell(tell, by)
		}
	}
	return out, err
}

func newBatchID() string {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// discarded is what discardOne leaves for the telling after the commit.
type discarded struct {
	tid           int64
	title, reason string
	said, owner   string
	notes         []told
	freed         []string
	forget        []db.InboxFile
	cancelled     []Retired
	withdrew      []any // the requests it closed, as the record keeps them
	sayStop       bool
}

func (d *discarded) tell(tell Teller, by string) {
	if d.owner != "" && tell != nil {
		tell.Cancelled(d.tid, by, d.owner, d.sayStop, d.cancelled)
		tell.Discarded(d.owner, d.notes, d.freed, d.forget)
	}
}

// discardOne throws a task away before it is done (actions.discard_task): refused while a child is open, while it waits on an open
// blocker, or while a request is still out on it - unless stop (the founder's cancel) or a lead discards in its own team tree, which
// withdraws the open requests instead. What follows the commit - the says typed, the owner's next task, the closed requests' inbox files
// - is the Teller's (discarded.tell). own is a withdraw: only the asker may, since anyone else must say why. batch names the batch this
// discard is one of, "" for none.
func discardOne(tx *sql.Tx, home, by string, tid int64, reason, batch string, stop, own bool) (*discarded, error) {
	d := &discarded{tid: tid}
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
	// The asks its comments raised end with it, and are no request left open.
	if err := closeMemoAsks(tx, "withdrawn", "id IN (SELECT request_id FROM task_requests WHERE task_id = ?)", tid); err != nil {
		return nil, err
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
	verdict := CancelVerdict
	if by != human {
		verdict = fmt.Sprintf("withdrawn by %s: %s", by, said)
	}
	stops := stop && by == human
	if !stops && by != human && len(live) > 0 {
		if stops, err = leadsTreeOf(home, by, t); err != nil {
			return nil, err
		}
	}
	// A request that already landed is held by its agent and no cancel can take it back: the asker or the founder discards the task
	// all the same, the hold ends with it and the holder is told.
	var holders []string
	if !stops && (by == who || by == human) && allLanded(live) {
		for _, r := range live {
			holders = append(holders, r.Target)
		}
		live = nil
	}
	inHand := false
	var withdrew []any
	if stops && len(live) > 0 { // the open requests are cancelled instead of refusing
		cur, _, err := db.CurrentTask(tx, t.Owner)
		if err != nil {
			return nil, err
		}
		delivered := false
		for _, r := range live {
			delivered = delivered || r.DeliveredAt.Valid
		}
		inHand = cur == tid || delivered
		d.sayStop = inHand && t.Owner != human && !delivered
		for _, r := range live {
			if r.State != "queued" {
				continue // landed: held by its agent, told to stop below; nothing to cancel
			}
			plan, err := cancelRequest(tx, r, by, verdict)
			if err != nil {
				return nil, err
			}
			if plan == nil {
				return nil, refusal(fmt.Sprintf("task T%d not discarded: %s was closed while this ran - nothing to cancel", tid, db.MsgName(tx, r.ID)))
			}
			withdrew = append(withdrew, r.ID)
			if plan.RID != 0 {
				d.cancelled = append(d.cancelled, *plan)
			} else {
				d.forget = append(d.forget, db.InboxFile{Target: r.Target, RID: r.ID})
			}
		}
	} else if len(live) > 0 {
		var open []string
		for _, r := range live {
			open = append(open, fmt.Sprintf("%s (with %s)", db.MsgName(tx, r.ID), r.Target))
		}
		if allLanded(live) {
			return nil, refusal(fmt.Sprintf("task T%d is in its holder's hands (%s) - only %s, who it answers to, or the founder can discard it now",
				tid, strings.Join(open, ", "), who))
		}
		return nil, refusal(fmt.Sprintf("task T%d still has a request open: %s - withdraw or cancel it first (the founder cancels it in the app), "+
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
	payload := []kv{{"task_id", tid}, {"reason", said}}
	if d.withdrew = withdrew; len(withdrew) > 0 {
		payload = append(payload, kv{"withdrew", withdrew})
	}
	if batch != "" {
		payload = append(payload, kv{"batch", batch})
	}
	if err := addEvent(tx, by, "discard_task", payload...); err != nil {
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
	also = append(also, holders...)
	ended, gone, err := endTask(tx, home, tid, by, t, strings.TrimRight(fmt.Sprintf("T%d discarded by %s: %s", tid, byWord, said), ": "), false, also)
	if err != nil {
		return nil, err
	}
	d.notes, d.forget = ended, append(d.forget, gone...)
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
	d.notes, d.freed, d.owner = append(d.notes, cleared...), owners, t.Owner
	d.title, d.reason, d.said = t.Title, said, fmt.Sprintf("task T%d discarded by %s: %s", tid, by, said)
	return d, nil
}

// leadsTreeOf is whether by leads a team whose tree holds the task: its owner or opener is by, or reports to by up the managers' chain.
func leadsTreeOf(home, by string, t *task) (bool, error) {
	tm, err := team.LoadTeam(home)
	if err != nil {
		return false, nil // no team file: nobody leads anyone
	}
	if _, lead := tm.Managers()[by]; !lead {
		return false, nil
	}
	for _, x := range []string{t.Owner, t.OpenedBy} {
		for seen := map[string]bool{}; x != "" && x != human && !seen[x]; x = tm.ManagerOf(x) {
			if x == by {
				return true, nil
			}
			seen[x] = true
		}
	}
	return false, nil
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

// cancelRequest is the write of a cancel (team/ask.py cancel) by the founder or a lead for one queued request: by's own undelivered one is
// withdrawn (plan.RID 0), any other closes as rejected under verdict. nil when it was closed while this ran. Neither settles the task: the
// discard calling this ends it.
func cancelRequest(tx *sql.Tx, r linked, by, verdict string) (*Retired, error) {
	if r.Requester == by && !r.DeliveredAt.Valid {
		ok, err := db.RetireIfUndelivered(tx, r.ID, "withdrawn", "")
		if err != nil || !ok {
			return nil, err
		}
		if err := addEvent(tx, by, "withdraw", kv{"request_id", r.ID}, kv{"target", r.Target}, kv{"reason", nil}); err != nil {
			return nil, err
		}
		return &Retired{}, nil
	}
	ok, err := db.CloseIfQueued(tx, r.ID, "rejected", verdict)
	if err != nil || !ok {
		return nil, err
	}
	if err := addEvent(tx, r.Requester, "closed_under", kv{"request_id", r.ID}, kv{"target", r.Target}); err != nil {
		return nil, err
	}
	if err := addEvent(tx, by, "cancel", kv{"request_id", r.ID}, kv{"target", r.Target}, kv{"requester", r.Requester}, kv{"delivered", r.DeliveredAt.Valid}); err != nil {
		return nil, err
	}
	return &Retired{RID: r.ID, Requester: r.Requester, Target: r.Target, Delivered: r.DeliveredAt.Valid, Verdict: verdict}, nil
}
