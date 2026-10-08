package supervisor

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/taskwrites"
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
