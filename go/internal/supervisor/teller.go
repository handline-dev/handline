package supervisor

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
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
