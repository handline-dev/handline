package supervisor

import (
	"database/sql"
	"fmt"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// The task subset of actions/tasks.py the supervisor calls: the owner's current task and advancing it, the "unblocked" and result notices,
// memo says. They run after the write that caused them has committed, on the database itself.

// LinkDelivery links a task to the request that delivers it, and makes it the owner's current task if it holds none yet.
func (e *Env) linkDelivery(q db.Q, tid int64, owner string, rid int64) {
	check(db.LinkRequest(q, tid, rid))
	if owner != db.Human {
		if _, held := ok2(db.CurrentTask(q, owner)); !held {
			check(db.SetCurrentTask(q, owner, tid, true))
		}
	}
}

// noteRefusal: a delivery of tid was refused. refused (the caller's, if it wants to say so) gets the text, and the event log gets it once -
// not again while the task's latest event is this same refusal, since every advance for its owner retries it.
func (e *Env) noteRefusal(q db.Q, agent string, tid int64, msg string, refused map[int64]string) {
	if refused != nil {
		refused[tid] = msg
	}
	var kind, payload sql.NullString
	err := q.QueryRow("SELECT kind, payload FROM events WHERE CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END = ? ORDER BY id DESC LIMIT 1", tid).Scan(&kind, &payload)
	if err != nil && err != sql.ErrNoRows {
		check(err)
	}
	if err == nil && kind.String == "task_deliver_refused" {
		var why sql.NullString
		check(q.QueryRow("SELECT json_extract(?, '$.why')", payload.String).Scan(&why))
		if why.Valid && why.String == msg {
			return
		}
	}
	must(db.AddEvent(q, agent, "task_deliver_refused", db.KV{K: "task_id", V: tid}, db.KV{K: "why", V: msg}))
}

// AdvanceCurrentTask moves the agent on to its next open task, by priority then age, once the current one stops being worth holding. True if
// anything changed: the caller's cue to push a delivery now. The founder holds no current-task row, so this is a no-op for it. A candidate with
// nothing out yet is delivered here, through the same Ask task_new used (on behalf of queued_by, read from our own db). A refusal is recorded and
// skipped; refused, if given, is filled with {task id: the refusal text} for a caller that owes it to whoever queued the task. Run it after the
// write that made it due has committed.
func (e *Env) AdvanceCurrentTask(q db.Q, agent string) (changed bool, refused map[int64]string, err error) {
	defer Catch(&err)
	refused = map[int64]string{}
	if agent == db.Human || must(db.CurrentTaskStillAsking(q, agent)) {
		return false, refused, nil
	}
	before, hadBefore := ok2(db.CurrentTask(q, agent))
	for _, w := range must(db.WaitingTasks(q, agent)) {
		if w.Req != nil {
			check(db.SetCurrentTask(q, agent, w.Task.ID, true))
			check(db.PromoteToFront(q, w.Req.ID))
			return true, refused, nil
		}
		out, aerr := e.Ask(q, AskIn{Target: agent, Body: w.Task.Title, Requester: w.Task.QueuedBy.String, Kind: "task", OnBehalfOf: true, Notify: true})
		if aerr != nil {
			return false, refused, aerr
		}
		msg := out.Finish()
		if !out.OK {
			e.noteRefusal(q, agent, w.Task.ID, msg, refused)
			continue
		}
		e.linkDelivery(q, w.Task.ID, agent, out.RID)
		check(db.SetCurrentTask(q, agent, w.Task.ID, true))
		return true, refused, nil
	}
	// Nothing to advance to. A current task that is merely blocked, and not yet done, stays current; a done task can carry a stale blocker too,
	// and that one really is finished.
	if hadBefore {
		if row := must(db.GetTask(q, before)); row != nil && !row.DoneEvidence.Valid && len(must(db.TaskBlockers(q, before))) > 0 {
			return false, refused, nil
		}
	}
	check(db.SetCurrentTask(q, agent, 0, false))
	return hadBefore, refused, nil
}

// QueueDeliver is what queuing does after its write commits: the task goes out now if its owner is free. row is the task as it was before.
func (e *Env) QueueDeliver(q db.Q, tid int64, by string, queued bool, row *db.Task) (msg string, err error) {
	defer Catch(&err)
	if queued && row.Owner == db.Human {
		// The founder holds no current task, so nothing advances it: queuing is putting the card back on their board.
		out, aerr := e.Ask(q, AskIn{Target: db.Human, Body: row.Title, Requester: row.OpenedBy, Kind: "task", OnBehalfOf: true, Notify: true})
		if aerr != nil {
			return "", aerr
		}
		text := out.Finish()
		if !out.OK {
			return fmt.Sprintf("task T%d is queued (%s) but its card was not raised: %s", tid, by, text), nil
		}
		e.linkDelivery(q, tid, db.Human, out.RID)
		return fmt.Sprintf("task T%d is queued (%s) - its card is on the founder's board", tid, by), nil
	}
	word := "draft"
	if queued {
		word = "queued"
		// advance's own answer says whether the owner's current task moved on at all, not whether it landed on tid; the task's state says which happened.
		changed, refused, aerr := e.AdvanceCurrentTask(q, row.Owner)
		if aerr != nil {
			return "", aerr
		}
		if changed {
			e.Push(q, row.Owner, 0)
		}
		if st := must(db.TaskState(q, must(db.GetTask(q, tid)))); st == "doing" {
			return fmt.Sprintf("task T%d is queued (%s) - %s was free, so it went out now", tid, by, row.Owner), nil
		}
		if why, ok := refused[tid]; ok { // queued, but not sent, and the approver is told why
			return fmt.Sprintf("task T%d is queued (%s) but was not sent to %s: %s", tid, by, row.Owner, why), nil
		}
	}
	return fmt.Sprintf("task T%d is %s (%s)", tid, word, by), nil
}

// ---------------------------------------------------------------- unblocked and result notices

var urgentResult = regexp.MustCompile(`(?i)^(fail(ed|ure)?|blocked|fatal)\b`)

// UrgentResult is whether a task's closing words say it failed or is blocked: it needs whoever waits on it now, not in a digest.
func UrgentResult(result string) bool { return urgentResult.MatchString(strings.TrimSpace(result)) }

// notifyUnblocked is told once, as a durable request linked to the task. Never called when the closer is the owner.
func (e *Env) notifyUnblocked(q db.Q, depTID int64, owner, closer string, closedTID int64, how string) db.Notice {
	text := fmt.Sprintf("task T%d is unblocked - T%d, its last open blocker, %s", depTID, closedTID, how)
	closed := must(db.GetTask(q, closedTID))
	if closed.DoneEvidence.Valid && closed.DoneEvidence.String != "" && !(closed.ParentTask.Valid && closed.ParentTask.Int64 == depTID) {
		text += ": " + closed.DoneEvidence.String // a child's result is told on its own, by childResultNotice
	}
	rid := must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: owner, Body: text, Response: "fire_and_forget", Kind: "say"}))
	check(db.LinkRequest(q, depTID, rid))
	must(db.AddEvent(q, closer, "task_unblocked", db.KV{K: "task_id", V: depTID}, db.KV{K: "request_id", V: rid}, db.KV{K: "closed_task", V: closedTID},
		db.KV{K: "after_request", V: must(db.NewestRequest(q))}))
	return db.Notice{RID: rid, To: owner, Text: text}
}

func oneSpace(s string) string { return strings.Join(strings.Fields(s), " ") }

// childResultNotice: when a child of dep's task ends, the task's owner is told how, whether or not other blockers remain. Once per finish (a
// reopen voids it).
func (e *Env) childResultNotice(q db.Q, dep *db.Task, by string, tid int64) *db.Notice {
	child := must(db.GetTask(q, tid))
	if child == nil || !child.ParentTask.Valid || child.ParentTask.Int64 != dep.ID || dep.Owner == by {
		return nil
	}
	var since sql.NullInt64
	check(q.QueryRow("SELECT MAX(id) FROM events WHERE kind = 'reopen_task' AND CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END = ?", tid).Scan(&since))
	var one int
	err := q.QueryRow("SELECT 1 FROM events WHERE kind = 'child_result_told' AND CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END = ? "+
		"AND json_extract(payload, '$.child') = ? AND id > ? LIMIT 1", dep.ID, tid, since.Int64).Scan(&one)
	if err == nil {
		return nil
	}
	if err != sql.ErrNoRows {
		check(err)
	}
	var text string
	switch {
	case child.DoneEvidence.Valid && child.DoneEvidence.String != "":
		text = fmt.Sprintf("T%d done by %s: %s", tid, child.Owner, oneSpace(child.DoneEvidence.String))
	case child.DiscardedBy.Valid && child.DiscardedBy.String != "":
		text = fmt.Sprintf("T%d discarded by %s: %s", tid, child.DiscardedBy.String, oneSpace(child.DiscardReason.String))
	default:
		return nil
	}
	text = strings.TrimRight(text, ": ")
	rid := must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: dep.Owner, Body: text, Response: "fire_and_forget", Kind: "say",
		Notice: !UrgentResult(child.DoneEvidence.String)}))
	check(db.LinkRequest(q, dep.ID, rid))
	must(db.AddEvent(q, by, "child_result_told", db.KV{K: "task_id", V: dep.ID}, db.KV{K: "child", V: tid}, db.KV{K: "request_id", V: rid}))
	return &db.Notice{RID: rid, To: dep.Owner, Text: text}
}

// ClearBlocksOn runs after tid is discarded: drop every block row naming it, and for each task that leaves with no open blocker, write a notice
// for its owner (skipped for the closer's own task) and collect that owner to advance. Dropping the row here is what makes "once" true. Returns
// the notices to deliver, and the owners to advance, once the caller's transaction commits.
func (e *Env) clearBlocksOn(q db.Q, tid int64, closer, how string) ([]db.Notice, []string) {
	var notices []db.Notice
	var freed []string
	seen := map[string]bool{}
	for _, depID := range must(db.TasksBlockedBy(q, tid)) {
		must(db.RemoveBlock(q, depID, tid))
		dep := must(db.GetTask(q, depID))
		if dep == nil || dep.Ended() {
			continue
		}
		if n := e.childResultNotice(q, dep, closer, tid); n != nil {
			notices = append(notices, *n)
		}
		if len(must(db.TaskBlockers(q, depID))) > 0 {
			continue
		}
		if !seen[dep.Owner] {
			seen[dep.Owner] = true
			freed = append(freed, dep.Owner)
		}
		if dep.Owner == closer {
			// No notice for the closer, but the task still comes due again.
			must(db.AddEvent(q, closer, "unblock_task", db.KV{K: "task_id", V: depID}, db.KV{K: "blocks_on", V: tid}, db.KV{K: "after_request", V: must(db.NewestRequest(q))}))
			continue
		}
		notices = append(notices, e.notifyUnblocked(q, depID, dep.Owner, closer, tid, how))
	}
	return notices, freed
}

// releasedAtDone: was dep already released for blocker tid's latest finish? A reopen voids it.
func (e *Env) releasedAtDone(q db.Q, tid, dep int64) bool {
	var since sql.NullInt64
	check(q.QueryRow("SELECT MAX(id) FROM events WHERE kind = 'reopen_task' AND CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END = ?", tid).Scan(&since))
	var one int
	err := q.QueryRow("SELECT 1 FROM events WHERE kind = 'released_at_done' AND CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END = ? "+
		"AND json_extract(payload, '$.blocks_on') = ? AND id > ? LIMIT 1", dep, tid, since.Int64).Scan(&one)
	if err != nil && err != sql.ErrNoRows {
		check(err)
	}
	return err == nil
}

// freeBlockedBy runs after tid is done: the tasks it blocked and that now have no open blocker are released - a notice for their owner (not
// by's own) and the owner to advance. Once per finish and dependent. The block rows stay, so a reopened tid blocks them again.
func (e *Env) freeBlockedBy(q db.Q, tid int64, by string) ([]db.Notice, []string) {
	var notices []db.Notice
	var freed []string
	seen := map[string]bool{}
	for _, depID := range must(db.TasksBlockedBy(q, tid)) {
		dep := must(db.GetTask(q, depID))
		if dep == nil || dep.Ended() || e.releasedAtDone(q, tid, depID) {
			continue
		}
		if n := e.childResultNotice(q, dep, by, tid); n != nil {
			notices = append(notices, *n)
		}
		free := len(must(db.TaskBlockers(q, depID))) == 0
		told := free && dep.Owner != by // the one who finished it does not need telling
		must(db.AddEvent(q, by, "released_at_done", db.KV{K: "task_id", V: depID}, db.KV{K: "blocks_on", V: tid}, db.KV{K: "freed", V: free}, db.KV{K: "told", V: told}))
		if free && !seen[dep.Owner] {
			seen[dep.Owner] = true
			freed = append(freed, dep.Owner)
		}
		if told {
			notices = append(notices, e.notifyUnblocked(q, depID, dep.Owner, by, tid, "done"))
		}
	}
	return notices, freed
}

// deliverUnblocked is the half of a release that happens after the write: tell the owners, and give each freed owner its next task now. An
// "unblocked" notice is typed (it tells a waiting owner to go on); a result notice is not - it rides on the owner's next delivery.
func (e *Env) deliverUnblocked(q db.Q, notices []db.Notice, freed []string) {
	for _, n := range notices {
		queued := must(db.GetRequest(q, n.RID)).State == "queued" // a notice is born landed
		if queued {
			must(db.MarkSendNow(q, n.RID))
		}
		if err := e.deliver(n.RID, db.NoticeRequester, n.To, n.Text, "", team.Decision{Allowed: true, Reason: "task unblocked", Kind: ptr("say"), Response: "fire_and_forget"}); err != nil {
			e.log("  #%d: the inbox file for %s was not written: %v", n.RID, n.To, err)
		}
		if queued {
			e.Push(q, n.To, n.RID)
		}
	}
	for _, owner := range freed {
		if changed, _, err := e.AdvanceCurrentTask(q, owner); err != nil {
			panic(dbFailure{err})
		} else if changed {
			e.Push(q, owner, 0)
		}
	}
}

// ReleaseEndedBlockers releases the tasks a done or discarded task was still blocking, the way a done or a discard releases them. A decision task
// settles with its card at the db layer, which cannot run the release: its dependents were left with a block row, no notice and no next turn. Read
// from the rows left behind. One blocker that fails is logged and left for the next tick. Returns the tasks released.
func (e *Env) ReleaseEndedBlockers() (freed []int64, err error) {
	defer Catch(&err)
	for _, step := range []struct{ query, how string }{
		{"SELECT DISTINCT b.blocks_on FROM task_blocks b JOIN tasks t ON t.id = b.blocks_on WHERE t.discarded_by IS NOT NULL", "discarded"},
		{"SELECT DISTINCT b.blocks_on FROM task_blocks b JOIN tasks t ON t.id = b.blocks_on JOIN tasks d ON d.id = b.task_id " +
			"WHERE t.done_evidence IS NOT NULL AND t.discarded_by IS NULL AND d.done_evidence IS NULL AND d.discarded_by IS NULL", "done"},
	} {
		for _, tid := range must(idsOf(e.DB, step.query)) {
			waiting, ferr := e.releaseOne(tid, step.how)
			if ferr != nil {
				e.log("  T%d: releasing the tasks it blocked failed: %v", tid, ferr)
				continue
			}
			freed = append(freed, waiting...)
		}
	}
	return freed, nil
}

func (e *Env) releaseOne(tid int64, how string) (waiting []int64, err error) {
	defer Catch(&err)
	row := must(db.GetTask(e.DB, tid))
	tx, err := e.DB.Begin()
	if err != nil {
		return nil, err
	}
	var notices []db.Notice
	var owners []string
	func() {
		defer func() {
			if r := recover(); r != nil {
				tx.Rollback()
				panic(r)
			}
		}()
		waiting = must(db.TasksBlockedBy(tx, tid))
		if how == "discarded" {
			notices, owners = e.clearBlocksOn(tx, tid, row.DiscardedBy.String, how)
		} else {
			notices, owners = e.freeBlockedBy(tx, tid, row.Owner)
		}
		check(tx.Commit())
	}()
	e.deliverUnblocked(e.DB, notices, owners)
	return waiting, nil
}

// ---------------------------------------------------------------- memos

// TypeSay writes a say from the task board to owner, typed at once.
func (e *Env) typeSay(q db.Q, owner, body, why string) {
	rid := must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: owner, Body: body, Response: "fire_and_forget", Kind: "say"}))
	must(db.MarkSendNow(q, rid))
	if err := e.deliver(rid, db.NoticeRequester, owner, body, "", team.Decision{Allowed: true, Reason: why, Kind: ptr("say"), Response: "fire_and_forget"}); err != nil {
		e.log("  #%d: the inbox file for %s was not written: %v", rid, owner, err)
	}
	e.Push(q, owner, rid)
}

// TellMemoHolder: a memo changing on a task that is doing is told to its holder at once, typed like a say; on any other task, or when the
// holder made the change, nothing is sent.
func (e *Env) TellMemoHolder(q db.Q, tid int64, by, what, text string) (err error) {
	defer Catch(&err)
	row := must(db.GetTask(q, tid))
	if row == nil || row.Owner == db.Human || row.Owner == by || must(db.TaskState(q, row)) != "doing" {
		return nil
	}
	body := fmt.Sprintf("T%d memo %s by %s", tid, what, by)
	if text != "" {
		t := textsafe.Oneline(text)
		if r := []rune(t); len(r) > 400 {
			t = string(r[:400])
		}
		body += ": " + t
	}
	e.typeSay(q, row.Owner, body, "task memo")
	return nil
}
