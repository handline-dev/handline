package supervisor

import (
	"database/sql"
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/delivery"
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
	e.tellRefusal(q, agent, tid, msg)
}

// tellRefusal tells the owner's manager (the founder when it has none) that tid sits queued because its delivery was refused.
func (e *Env) tellRefusal(q db.Q, agent string, tid int64, msg string) {
	to := db.Human
	if t, err := team.LoadTeam(e.Home); err == nil && t.ManagerOf(agent) != "" {
		to = t.ManagerOf(agent)
	}
	text := fmt.Sprintf("T%d could not be delivered to %s and is waiting: %s", tid, agent, msg)
	rid := must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: to, Body: text, Response: "fire_and_forget", Kind: "say"}))
	check(db.LinkRequest(q, tid, rid))
	e.Push(q, to, rid)
}

// selfDelivery is a task handed back to the agent that queued it: not a request to itself but its own work, carrying the answers that came
// back on the task so far.
func (e *Env) selfDelivery(q db.Q, agent string, task *db.Task) int64 {
	body := task.Title
	for _, linked := range must(db.TaskRequests(q, task.ID)) {
		r := must(db.GetRequest(q, linked.ID))
		if r.Answer.Valid && r.Answer.String != "" {
			body += fmt.Sprintf("\n\nanswer to %s: %s", db.MsgName(q, r.ID), r.Answer.String)
		}
	}
	return must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: agent, Body: body, Response: "fire_and_forget", Kind: "task"}))
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
		if e.makeCurrent(q, agent, w, refused) {
			return true, refused, nil
		}
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

// makeCurrent makes w the agent's current task and delivers it: its own queued request goes to the front, or a new one is asked. False when
// the routing refused the ask (noted in refused).
func (e *Env) makeCurrent(q db.Q, agent string, w db.WaitingTask, refused map[int64]string) bool {
	if w.Req != nil {
		check(db.SetCurrentTask(q, agent, w.Task.ID, true))
		check(db.PromoteToFront(q, w.Req.ID))
		return true
	}
	if w.Task.QueuedBy.String == agent {
		rid := e.selfDelivery(q, agent, w.Task)
		e.linkDelivery(q, w.Task.ID, agent, rid)
		check(db.SetCurrentTask(q, agent, w.Task.ID, true))
		e.Push(q, agent, rid)
		return true
	}
	out, aerr := e.Ask(q, AskIn{Target: agent, Body: w.Task.Title, Requester: w.Task.QueuedBy.String, Kind: "task", OnBehalfOf: true, Notify: true})
	check(aerr)
	msg := out.Finish()
	if !out.OK {
		e.noteRefusal(q, agent, w.Task.ID, msg, refused)
		return false
	}
	e.linkDelivery(q, w.Task.ID, agent, out.RID)
	check(db.SetCurrentTask(q, agent, w.Task.ID, true))
	return true
}

// AdvanceToDormantBlocker delivers, while the agent's current task is still asking, a task that current task waits on through a dormant
// self-block (one agent holds both, so the wait does not count) and that waits behind it: otherwise the held task waits on its own blocker
// and the blocker on the held task, and everything else of the agent's waits behind both. True when one was delivered.
func (e *Env) AdvanceToDormantBlocker(q db.Q, agent string) (changed bool, err error) {
	defer Catch(&err)
	cur, ok := ok2(db.CurrentTask(q, agent))
	if !ok {
		return false, nil
	}
	dormant := map[int64]bool{}
	for _, b := range must(db.DormantBlockers(q, cur)) {
		dormant[b] = true
	}
	if len(dormant) == 0 {
		return false, nil
	}
	refused := map[int64]string{}
	for _, w := range must(db.WaitingTasks(q, agent)) {
		if dormant[w.Task.ID] && e.makeCurrent(q, agent, w, refused) {
			return true, nil
		}
	}
	return false, nil
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
	return db.Notice{RID: rid, To: owner, Text: text, Unblocked: depTID}
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
		Notice: delivery.ClassOf(delivery.Result, false, delivery.Failed(child.DoneEvidence.String)) == delivery.ClassBatch}))
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
// "unblocked" notice is typed (it tells a waiting owner to go on), unless that task goes out to its owner in this step; a result notice is
// not - it rides on the owner's next delivery.
func (e *Env) deliverUnblocked(q db.Q, notices []db.Notice, freed []string) {
	moved := map[string]bool{}
	for _, owner := range freed {
		changed, _, err := e.AdvanceCurrentTask(q, owner)
		if err != nil {
			panic(dbFailure{err})
		}
		moved[owner] = changed
	}
	for _, n := range notices {
		if n.Unblocked != 0 && moved[n.To] {
			if cur, ok := ok2(db.CurrentTask(q, n.To)); ok && cur == n.Unblocked {
				must(db.RetireIfUndelivered(q, n.RID, "withdrawn", "the task itself went out to its owner in the same step", false))
				continue
			}
		}
		queued := must(db.GetRequest(q, n.RID)).State == "queued" // a notice is born landed
		if queued {
			must(db.MarkSendNow(q, n.RID))
		}
		if err := e.deliver(n.RID, db.NoticeRequester, n.To, n.Text, "", team.Decision{Allowed: true, Reason: "task unblocked", Kind: ptr("say"), Response: "fire_and_forget"}); err != nil {
			e.log("  request %d: the inbox file for %s was not written: %v", n.RID, n.To, err)
		}
		if queued {
			e.Push(q, n.To, n.RID)
		}
	}
	for _, owner := range freed {
		if moved[owner] {
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
	e.Push(q, owner, e.queueSay(q, owner, body, why))
}

// queueSay writes the say and its inbox file, and does not tell the owner: that is Push's, which a caller in a transaction runs after the commit.
func (e *Env) queueSay(q db.Q, owner, body, why string) int64 {
	rid := must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: owner, Body: body, Response: "fire_and_forget", Kind: "say"}))
	must(db.MarkSendNow(q, rid))
	if err := e.deliver(rid, db.NoticeRequester, owner, body, "", team.Decision{Allowed: true, Reason: why, Kind: ptr("say"), Response: "fire_and_forget"}); err != nil {
		e.log("  request %d: the inbox file for %s was not written: %v", rid, owner, err)
	}
	return rid
}

// TellMemo tells a memo's change to each of its readers (memoReaders). The reader it was said to (to, "" for none), a reader the memo names
// with @, and every reader when now are typed it at once like a say; any other reader gets it in its next digest (SweepDigests), and its
// lead hears of it if it waits an hour (SweepHeldMemos).
func (e *Env) TellMemo(q db.Q, tid int64, by, what, text, to string, now bool) (err error) {
	defer Catch(&err)
	row := must(db.GetTask(q, tid))
	if row == nil {
		return nil
	}
	body := memoLine(tid, what, by, text)
	named := e.mentioned(text)
	for _, a := range e.memoReaders(row, by, named) {
		if delivery.ClassOf(delivery.Memo, now || a == to || slices.Contains(named, a), false) == delivery.ClassNow {
			e.typeSay(q, a, body, "task memo")
		} else {
			rid := must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: a, Body: body, Response: "fire_and_forget", Kind: "say", Memo: true}))
			check(db.LinkRequest(q, tid, rid))
			e.log("  %s: T%d memo held for its next digest (request %d)", a, tid, rid)
		}
	}
	return nil
}

// memoReaders is whom a memo on row concerns, each once: who the task answers to (its reporter), its assignee whatever the task's state, and
// every registered agent the memo names with @. Never the writer, or the founder, who reads the board.
func (e *Env) memoReaders(row *db.Task, by string, mentioned []string) []string {
	named := append([]string{row.AnswersTo(), row.Owner}, mentioned...)
	skip := map[string]bool{"": true, by: true, db.Human: true}
	var out []string
	for _, a := range named {
		if !skip[a] {
			skip[a] = true
			out = append(out, a)
		}
	}
	return out
}

// mentioned is every registered agent text names with @.
func (e *Env) mentioned(text string) []string {
	var out []string
	for _, m := range mentionRe.FindAllStringSubmatch(text, -1) {
		if name := strings.TrimRight(m[1], ".-"); e.registered(name) {
			out = append(out, name)
		}
	}
	return out
}

// memoLine is the line a memo's reader is told: `T<n> memo <what> by <who>: <text>`, the text on one line and cut at 400 runes, then its file
// lines whole.
func memoLine(tid int64, what, by, text string) string {
	line := fmt.Sprintf("T%d memo %s by %s", tid, what, by)
	if text == "" {
		return line
	}
	text, files := attachments.SplitFileLines(text)
	t := textsafe.Oneline(text)
	if r := []rune(t); len(r) > 400 {
		t = attachments.KeepRefs(string(r[:400]), t)
	}
	return strings.Join(append([]string{line + ": " + t}, files...), "\n")
}

// MemoLeadS is how long a memo held for its reader's digest may go unread before the reader's lead is told.
const MemoLeadS = 3600

// SweepHeldMemos tells the reader's lead once, in its digest, of each memo held for the reader's digest that has gone MemoLeadS unread. The
// memo stays held for the reader.
func (e *Env) SweepHeldMemos(now int64) (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	rows := must(db.UnreadMemosBefore(q, now-MemoLeadS))
	if len(rows) == 0 {
		return nil
	}
	t := must(team.LoadTeam(e.Home))
	for _, r := range rows {
		key := fmt.Sprintf("memo_lead:%d", r.ID)
		if must(db.Cursor(q, key)) != 0 {
			continue
		}
		check(db.SetCursor(q, key, 1))
		if lead := t.SupervisorOf(r.Target); lead != "" && lead != db.Human && e.registered(lead) {
			must(e.Notice(q, lead, fmt.Sprintf("%s has not read this memo in an hour, held for its next digest (%s): %s", r.Target, db.MsgName(q, r.ID), r.Body), "memo unread 1h"))
		}
	}
	return nil
}

// mentionRe is an @name in a memo: at the start or after a character that is not part of a name, so an email address names nobody.
var mentionRe = regexp.MustCompile(`(?:^|[^A-Za-z0-9._@-])@([A-Za-z0-9][A-Za-z0-9._-]*)`)
