package supervisor

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/team"
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
			said = append(said, fmt.Sprintf("#%d ended; %s told to stop", r.RID, r.Target))
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

// HandoffAsk routes the request a handoff sends to the new owner (kind task), in the transaction of the move; within is the move, run once the
// ask is allowed. Nothing is typed here: the push waits for the commit, after the move, in HandedOver.
func (e *Env) HandoffAsk(tx *sql.Tx, from, to, body string, within func(rid int64) error) (*taskwrites.Asked, error) {
	out, err := e.Ask(tx, AskIn{Target: to, Body: body, Requester: from, Kind: "task",
		Within: func(_ db.Q, rid int64, _ string) error { return within(rid) }})
	if err != nil {
		return nil, err
	}
	return &taskwrites.Asked{OK: out.OK, Msg: out.Message, RID: out.RID, After: out.After.Run}, nil
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
	e.tellParentOfSiblingMove(h.By, h.To, fmt.Sprintf("T%d handed by %s to %s: %s", h.Task, h.By, h.To, h.Title))
	return strings.Join(lines, "\n")
}

// tellParentOfSiblingMove: siblings give each other work without their manager in between; the manager is told as a notice, which rides on its
// next delivery and asks nothing of it.
func (e *Env) tellParentOfSiblingMove(me, to, text string) {
	mgr := team.ParentName(me)
	if mgr == "" || to == db.Human || team.ParentName(to) != mgr {
		return
	}
	known := false
	for _, a := range paths.ListAgents(e.Home) {
		known = known || a == mgr
	}
	if !known {
		return
	}
	rid, err := db.AddRequest(e.DB, db.NewRequest{Requester: db.NoticeRequester, Target: mgr, Body: text, Response: "fire_and_forget", Kind: "say", Notice: true})
	if err != nil {
		e.log("  telling %s of a move between siblings failed: %v", mgr, err)
		return
	}
	d := team.Decision{Allowed: true, Reason: "sibling move", Kind: ptr("say"), Response: "fire_and_forget"}
	if err := e.deliver(rid, db.NoticeRequester, mgr, text, "", d); err != nil {
		e.log("  #%d: the inbox file for %s was not written: %v", rid, mgr, err)
	}
}
