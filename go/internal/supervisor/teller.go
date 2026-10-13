package supervisor

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/textsafe"
)

// What the task writes (go/internal/taskwrites) leave to the telling layer once they have committed: Env satisfies taskwrites.Teller. Nothing
// here is raised to the caller, whose write is done: a failure is logged and the tick is the net.

// MemoChanged tells the holder of a doing task that a memo was added, edited or deleted (what), typed like a say.
func (e *Env) MemoChanged(tid int64, by, what, text string) {
	if err := e.TellMemoHolder(e.DB, tid, by, what, text); err != nil {
		e.log("  telling the holder of T%d about the memo failed: %v", tid, err)
	}
}

// QueueDelivered is what queuing does after its write commits: the task goes out now if its owner is free. It answers the sentence the reply
// carries for the task.
func (e *Env) QueueDelivered(tid int64, by string, queued bool) (msg string) {
	word := "draft"
	if queued {
		word = "queued"
	}
	msg = fmt.Sprintf("task T%d is %s (%s)", tid, word, by)
	row, err := db.GetTask(e.DB, tid)
	if err != nil || row == nil {
		e.log("  delivering T%d after it was queued failed: %v", tid, err)
		return msg
	}
	got, err := e.QueueDeliver(e.DB, tid, by, queued, row)
	if err != nil {
		e.log("  delivering T%d after it was queued failed: %v", tid, err)
		return msg
	}
	return got
}

// Discarded is what a discard does after it commits: the owner is moved on to its next task and told about it, the says the discard wrote are
// typed, the owners it freed advance, and the inbox files of the requests it closed go.
func (e *Env) Discarded(owner string, told []db.Notice, freed []string, forget []db.InboxFile) {
	var out string
	defer e.guard("telling after a discard", &out)
	for _, f := range forget {
		e.ForgetInbox(f.Target, f.RID)
	}
	changed, _, err := e.AdvanceCurrentTask(e.DB, owner)
	if err != nil {
		e.log("  moving %s on after a discard failed: %v", owner, err)
	} else if changed {
		e.Push(e.DB, owner, 0)
	}
	e.deliverUnblocked(e.DB, told, freed)
}

// Released is what a release does after it commits (actions.release_task): the owner of a held request is typed to stop, and each queued
// request it closed is told like any retirement (the target told to stop if it had it, the requester told, the queue moved up). It answers
// a phrase for each, the first line of what the retirement says.
func (e *Env) Released(tid int64, why string, retired []taskwrites.Retired) []string {
	var out string
	defer e.guard("telling after a release", &out)
	said := make([]string, 0, len(retired))
	for _, r := range retired {
		if r.Held {
			e.typeSay(e.DB, r.Target, why+" - stop work on it", "task released")
			if r.RID == 0 {
				said = append(said, r.Target+" told to stop")
			} else {
				said = append(said, fmt.Sprintf("#%d ended; %s told to stop", r.RID, r.Target))
			}
			continue
		}
		row, err := db.GetRequest(e.DB, r.RID)
		if err != nil || row == nil {
			e.log("  telling after T%d released #%d failed: %v", tid, r.RID, err)
			continue
		}
		target, _, extra, err := e.closeTell(e.DB, r.RID, row, why, r.Notify)
		if err != nil {
			e.log("  telling after T%d released #%d failed: %v", tid, r.RID, err)
		}
		line := fmt.Sprintf("#%d to %s retired", r.RID, target)
		if len(extra) > 0 {
			line += " (" + strings.Join(extra, "; ") + ")"
		}
		said = append(said, line)
	}
	return said
}

// Cancelled is what the founder's cancel does after it commits, ahead of the discard's own telling: each request it closed is told like any
// cancel (the target told to stop if it had it, the requester told, the queue moved up), and an owner holding the task without a delivery is
// typed to stop.
func (e *Env) Cancelled(tid int64, by, owner string, stop bool, closed []taskwrites.Retired) {
	var out string
	defer e.guard("telling after a cancel", &out)
	for _, r := range closed {
		row, err := db.GetRequest(e.DB, r.RID)
		if err != nil || row == nil {
			e.log("  telling after T%d was cancelled, #%d failed: %v", tid, r.RID, err)
			continue
		}
		if _, _, _, err := e.closeTell(e.DB, r.RID, row, CancelVerdict, r.Notify); err != nil {
			e.log("  telling after T%d was cancelled, #%d failed: %v", tid, r.RID, err)
		}
	}
	if stop {
		e.typeSay(e.DB, owner, fmt.Sprintf("T%d discarded by %s - stop work on it", tid, by), "task discarded")
	}
}

// Done is what a done task does after it commits (actions.mark_task_done): the inbox files of the requests it closed go, the owner is moved on
// and pushed its next task, the tasks it was the last blocker of are released, and the says it wrote are typed. A leftover request settled on
// an already done task only types its says.
func (e *Env) Done(tid int64, by, owner string, told []db.Notice, forget []db.InboxFile, finished bool) {
	var out string
	defer e.guard("telling after a done", &out)
	for _, f := range forget {
		e.ForgetInbox(f.Target, f.RID)
	}
	var freed []string
	if finished {
		if changed, _, err := e.AdvanceCurrentTask(e.DB, owner); err != nil {
			e.log("  moving %s on after T%d was done failed: %v", owner, tid, err)
		} else if changed {
			e.Push(e.DB, owner, 0)
		}
		var notices []db.Notice
		notices, freed = e.freeBlockedBy(e.DB, tid, by)
		told = append(told, notices...)
	}
	e.deliverUnblocked(e.DB, told, freed)
}

// Reported is what a done filed as a report does after it commits: the inbox files of the requests it closed go, the owner is moved on, and the
// report is typed to whoever the task answers to. The task is still open: nothing it blocks is released.
func (e *Env) Reported(tid int64, owner string, told []db.Notice, forget []db.InboxFile) {
	var out string
	defer e.guard("telling after a report", &out)
	for _, f := range forget {
		e.ForgetInbox(f.Target, f.RID)
	}
	e.deliverUnblocked(e.DB, told, []string{owner})
}

// Rejected is what a rejected report does after it commits: the note is typed to the owner and the task, queued again, goes out if the owner
// is free.
func (e *Env) Rejected(tid int64, by string, told []db.Notice) (out string) {
	defer e.guard("telling after a reject", &out)
	e.deliverUnblocked(e.DB, told, nil)
	return e.QueueDelivered(tid, by, true)
}

// TaskAsk routes the request that delivers a task, in the transaction of the write that opens or moves it; in.Within is that write, run once
// the ask is allowed. A handoff types nothing here (its push waits for the commit, after the move, in HandedOver); task.new asks for it.
func (e *Env) TaskAsk(tx *sql.Tx, in taskwrites.AskReq) (*taskwrites.Asked, error) {
	out, err := e.Ask(tx, AskIn{Target: in.To, Body: in.Body, Requester: in.From, Kind: in.Kind, NoCard: in.NoCard, Notify: in.Notify,
		Within: func(_ db.Q, rid int64, _ string) error { return in.Within(rid) }})
	if err != nil {
		return nil, err
	}
	return askedOf(out, in.Notify), nil
}

// askedOf is the answer a write hands back for an ask: the inbox file and the telling run in After, whose line (what the push said) joins the
// reply, as Finish does.
func askedOf(out *AskOut, notify bool) *taskwrites.Asked {
	return &taskwrites.Asked{OK: out.OK, Msg: out.Message, RID: out.RID, After: func() string {
		if extra := out.After.Run(); extra != "" && out.OK && notify {
			return extra
		}
		return ""
	}}
}

// TaskSay sends the founder's question about a task as a say, typed at once; within links it to the task in the transaction that writes it.
func (e *Env) TaskSay(tx *sql.Tx, to, body string, within func(rid int64, note string) error) (*taskwrites.Asked, error) {
	out, _, err := e.Say(tx, SayIn{Target: to, Body: body, Requester: db.Human, Notify: true,
		Within: func(_ db.Q, note string, rid int64) error { return within(rid, note) }})
	if err != nil {
		return nil, err
	}
	return askedOf(out, true), nil
}

// HandoffRetire closes a request of the old owner's as resolve does for a handoff: done, the requester not told, the decision task it is the
// card of not settled. A request closed in the meantime is left as it is.
func (e *Env) HandoffRetire(tx *sql.Tx, rid int64, verdict string) (func(), error) {
	r, ok, _, err := e.ResolveWrite(tx, ResolveIn{RID: rid, Verdict: verdict, NoSettle: true})
	if err != nil || !ok {
		return nil, err
	}
	return func() {
		if _, err := e.ResolveTell(e.DB, r); err != nil {
			e.log("  telling after #%d was handed over failed: %v", rid, err)
		}
	}, nil
}

// HandedOver is what a handoff does after it commits (actions.task_handoff): the inbox file of its request is written, the requests it closed
// are told, the old owner is moved on if it held the task, the new owner is typed the task, and a manager is told when siblings pass work.
func (e *Env) HandedOver(h taskwrites.Handover) (out string) {
	defer e.guard("telling after a handoff", &out)
	var lines []string
	add := func(s string) {
		if s != "" {
			lines = append(lines, s)
		}
	}
	if h.After != nil {
		add(h.After())
	}
	for _, told := range h.Retired {
		told()
	}
	if h.WasCurrent {
		if changed, _, err := e.AdvanceCurrentTask(e.DB, h.From); err != nil {
			e.log("  moving %s on after T%d was handed over failed: %v", h.From, h.Task, err)
		} else if changed {
			e.Push(e.DB, h.From, 0)
		}
	}
	if h.To != db.Human { // the founder holds no current task and is not typed at
		add(e.Push(e.DB, h.To, h.RID))
	}
	return strings.Join(lines, "\n")
}

// Edited is what an edit does after it commits (rpc_tasks._edit): a changed body told to the holder, a task that changed hands sent on, and an
// owner whose last blocker the edit cleared moved on.
func (e *Env) Edited(ed taskwrites.Edited) {
	var out string
	defer e.guard("telling after an edit", &out)
	if ed.Body {
		e.tellBodyEdited(e.DB, ed.Task, ed.By)
	}
	if ed.WasOwner != "" {
		e.sendMovedTask(e.DB, ed.Task, ed.WasOwner, ed.By)
	}
	if ed.Freed != "" {
		if changed, _, err := e.AdvanceCurrentTask(e.DB, ed.Freed); err != nil {
			e.log("  moving %s on after an unblock failed: %v", ed.Freed, err)
		} else if changed {
			e.Push(e.DB, ed.Freed, 0)
		}
	}
}

// Notified delivers the notices a task write left for the task's opener and assignee.
func (e *Env) Notified(told []db.Notice) {
	var out string
	defer e.guard("telling of a task change", &out)
	e.deliverUnblocked(e.DB, told, nil)
}

// tellBodyEdited: after an edit changed a task's body, the holder is told at once, typed like a say, when the task is being worked.
func (e *Env) tellBodyEdited(q db.Q, tid int64, by string) {
	row := must(db.GetTask(q, tid))
	var latest sql.NullString
	err := q.QueryRow("SELECT json_extract(payload, '$.body.new') FROM events WHERE kind = 'edit_task' AND json_valid(payload) AND "+
		"json_extract(payload, '$.task_id') = ? AND json_type(payload, '$.body') = 'object' ORDER BY id DESC LIMIT 1", tid).Scan(&latest)
	if err == sql.ErrNoRows || row == nil || row.Owner == db.Human || row.Owner == by || must(db.TaskState(q, row)) != "doing" {
		return
	}
	check(err)
	e.typeSay(q, row.Owner, fmt.Sprintf("T%d body edited by %s: %s", tid, by, firstRunes(textsafe.Oneline(latest.String), 400)), "task body edited")
}

// sendMovedTask (actions.send_moved_task): after an edit gave the task to a new owner, the old owner's request for it is taken back and the
// new owner is asked the same brief now, as task.new would; an old owner that had the task in hand is told to stop. No-op if the owner did not change.
func (e *Env) sendMovedTask(q db.Q, tid int64, old, by string) {
	row := must(db.GetTask(q, tid))
	if row == nil || row.Owner == old {
		return
	}
	owner := row.Owner
	var taken []*db.Request
	if live := must(db.LiveDelivery(q, tid, old)); live != nil {
		taken = append(taken, live)
	}
	cur, held := ok2(db.CurrentTask(q, old))
	inHand := held && cur == tid
	for _, r := range taken {
		inHand = inHand || r.DeliveredAt.Valid && r.DeliveredAt.Int64 != 0
		if r.State == "queued" {
			if _, _, err := e.Resolve(q, ResolveIn{RID: r.ID, Verdict: "moved to " + owner, State: "withdrawn"}); err != nil {
				panic(dbFailure{err})
			}
		}
	}
	check(db.EndHeld(q, tid, old))
	if cur, held := ok2(db.CurrentTask(q, old)); held && cur == tid {
		check(db.SetCurrentTask(q, old, 0, false))
		if changed, _, err := e.AdvanceCurrentTask(q, old); err != nil {
			panic(dbFailure{err})
		} else if changed {
			e.Push(q, old, 0)
		}
	}
	if inHand && old != db.Human {
		e.typeSay(q, old, fmt.Sprintf("T%d moved to %s by %s - stop work on it", tid, owner, by), "task moved")
	}
	if len(taken) > 0 && owner != db.Human {
		asker := taken[0].Requester
		if asker != owner { // given to the one who asked: advance starts it as its own below
			out, err := e.Ask(q, AskIn{Target: owner, Body: taken[0].Body, Requester: asker, Kind: "task", OnBehalfOf: true})
			if err != nil {
				panic(dbFailure{err})
			}
			msg := out.Finish()
			if out.OK {
				e.linkDelivery(q, tid, owner, out.RID)
				e.Push(q, owner, 0)
				return
			}
			e.noteRefusal(q, owner, tid, msg, nil)
		}
		must(db.SetTaskQueued(q, tid, asker)) // the tick tries again, with the title
	}
	if changed, _, err := e.AdvanceCurrentTask(q, owner); err != nil {
		panic(dbFailure{err})
	} else if changed {
		e.Push(q, owner, 0)
	}
}
