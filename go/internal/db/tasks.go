package db

import (
	"database/sql"
	"fmt"
	"strings"
)

// OpenSQL is a task that is neither done nor discarded.
const OpenSQL = "done_evidence IS NULL AND discarded_by IS NULL"

// DefaultPriority is the priority a task is opened with.
const DefaultPriority = 2

// Task is one tasks row.
type Task struct {
	ID, CreatedAt, Priority                                int64
	Title, Owner, OpenedBy                                 string
	ParentTask, DoneAt, EditedAt, ClosedAt, DiscardedAt    sql.NullInt64
	MergedInto, QueuedAt, TimeoutS, TimeoutAt, DeliveredAt sql.NullInt64
	SentAt                                                 sql.NullInt64
	DoneEvidence, DoneVia, EditedBy, ClosedBy, DiscardedBy sql.NullString
	DiscardReason, QueuedBy, Expects                       sql.NullString
}

const taskCols = "id, title, owner, parent_task, opened_by, done_evidence, done_at, done_via, edited_by, edited_at, created_at, closed_by, closed_at, " +
	"discarded_by, discard_reason, discarded_at, priority, merged_into, queued_by, queued_at, timeout_s, timeout_at, delivered_at, sent_at, expects"

func (t *Task) dest() []any {
	return []any{&t.ID, &t.Title, &t.Owner, &t.ParentTask, &t.OpenedBy, &t.DoneEvidence, &t.DoneAt, &t.DoneVia, &t.EditedBy, &t.EditedAt, &t.CreatedAt,
		&t.ClosedBy, &t.ClosedAt, &t.DiscardedBy, &t.DiscardReason, &t.DiscardedAt, &t.Priority, &t.MergedInto, &t.QueuedBy, &t.QueuedAt,
		&t.TimeoutS, &t.TimeoutAt, &t.DeliveredAt, &t.SentAt, &t.Expects}
}

// Ended is db.task_ended: done or discarded.
func (t *Task) Ended() bool { return t.DoneEvidence.String != "" || t.DiscardedBy.String != "" }

func taskRows(q Q, query string, args ...any) ([]*Task, error) {
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Task
	for rows.Next() {
		t := &Task{}
		if err := rows.Scan(t.dest()...); err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// GetTask is db.get_task: nil when there is no such task.
func GetTask(q Q, tid int64) (*Task, error) {
	ts, err := taskRows(q, "SELECT "+taskCols+" FROM tasks WHERE id = ?", tid)
	if err != nil || len(ts) == 0 {
		return nil, err
	}
	return ts[0], nil
}

// OpenTasks is db.open_tasks.
func OpenTasks(q Q) ([]*Task, error) {
	return taskRows(q, "SELECT "+taskCols+" FROM tasks WHERE "+OpenSQL+" ORDER BY priority, id")
}

// OpenChildTasks is db.open_child_tasks.
func OpenChildTasks(q Q, tid int64) ([]*Task, error) {
	return taskRows(q, "SELECT "+taskCols+" FROM tasks WHERE parent_task = ? AND "+OpenSQL+" ORDER BY priority, id", tid)
}

// TasksForRequest is db.tasks_for_request: the tasks a request is linked to, oldest first.
func TasksForRequest(q Q, rid int64) ([]*Task, error) {
	return taskRows(q, "SELECT t.id, t.title, t.owner, t.parent_task, t.opened_by, t.done_evidence, t.done_at, t.done_via, t.edited_by, t.edited_at, t.created_at, t.closed_by, t.closed_at, "+
		"t.discarded_by, t.discard_reason, t.discarded_at, t.priority, t.merged_into, t.queued_by, t.queued_at, t.timeout_s, t.timeout_at, t.delivered_at, t.sent_at, t.expects "+
		"FROM tasks t JOIN task_requests l ON l.task_id = t.id WHERE l.request_id = ? ORDER BY t.id", rid)
}

// TaskRequests is db.task_requests: the requests linked to a task, as they are now.
func TaskRequests(q Q, tid int64) ([]*Request, error) {
	return requestRows(q, "SELECT r.id, r.ts, r.requester, r.target, r.body, r.files, r.relation, r.response, r.kind, r.chain_depth, r.parent_id, r.chain_id, r.state, r.queue_order, "+
		"r.delivered_at, r.typed_at, r.typed_session, r.landed_at, r.send_now, r.notice, r.read_at, r.state_at, r.moot_by, r.edge_ok, r.settles "+
		"FROM requests r JOIN task_requests l ON l.request_id = r.id WHERE l.task_id = ? ORDER BY r.id", tid)
}

// LiveDelivery is db.live_delivery: the request that asks owner to do the task (the latest task delivery to that owner, queued or
// landed and not answered), nil when none.
func LiveDelivery(q Q, tid int64, owner string) (*Request, error) {
	rows, err := TaskRequests(q, tid)
	if err != nil {
		return nil, err
	}
	var last *Request
	for _, r := range rows {
		if r.Kind.String == "task" && r.Target == owner && (r.State == "queued" || r.State == Landed && r.QueueOrder.Valid) {
			last = r
		}
	}
	return last, nil
}

// TaskBlockers is db.task_blockers: the tasks still blocking tid.
func TaskBlockers(q Q, tid int64) ([]*Task, error) {
	return taskRows(q, "SELECT t.id, t.title, t.owner, t.parent_task, t.opened_by, t.done_evidence, t.done_at, t.done_via, t.edited_by, t.edited_at, t.created_at, t.closed_by, t.closed_at, "+
		"t.discarded_by, t.discard_reason, t.discarded_at, t.priority, t.merged_into, t.queued_by, t.queued_at, t.timeout_s, t.timeout_at, t.delivered_at, t.sent_at, t.expects "+
		"FROM tasks t JOIN task_blocks b ON b.blocks_on = t.id WHERE b.task_id = ? AND b.dormant = 0 AND t.discarded_by IS NULL AND t.done_evidence IS NULL ORDER BY t.id", tid)
}

// NewTask is db.new_task. The clock of timeoutS starts when the task is doing, so timeout_at stays NULL.
func NewTask(q Q, title, owner, openedBy string, parent int64, priority int64, expects string, timeoutS int64) (int64, error) {
	var p, e, ts any
	if parent != 0 {
		p = parent
	}
	if expects != "" {
		e = expects
	}
	if timeoutS != 0 {
		ts = timeoutS
	}
	res, err := q.Exec("INSERT INTO tasks(title, owner, parent_task, opened_by, created_at, priority, expects, timeout_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
		title, owner, p, openedBy, Now(), priority, e, ts)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// LinkRequest is db.link_request.
func LinkRequest(q Q, tid, rid int64) error {
	if _, err := q.Exec("INSERT OR IGNORE INTO task_requests(task_id, request_id) VALUES (?, ?)", tid, rid); err != nil {
		return err
	}
	return RefreshSent(q, tid)
}

// RefreshSent is db.refresh_sent: sent_at is set once while a request that asks something is out on the task, cleared
// when none is left.
func RefreshSent(q Q, tid int64) error {
	_, err := q.Exec("UPDATE tasks SET sent_at = CASE WHEN EXISTS "+
		"(SELECT 1 FROM task_requests l JOIN requests r ON r.id = l.request_id WHERE l.task_id = tasks.id "+
		"AND (r.state = 'queued' OR (r.state = 'landed' AND r.kind = 'task' AND r.target = tasks.owner AND r.queue_order IS NOT NULL))) "+
		"THEN COALESCE(sent_at, ?) ELSE NULL END WHERE id = ?", Now(), tid)
	return err
}

// RefreshSentOfRequest is db.refresh_sent_of_request.
func RefreshSentOfRequest(q Q, rid int64) error {
	tids, err := ids(q, "SELECT task_id FROM task_requests WHERE request_id = ?", rid)
	if err != nil {
		return err
	}
	for _, t := range tids {
		if err := RefreshSent(q, t); err != nil {
			return err
		}
	}
	return nil
}

// EndHeld is db.end_held: the task no longer holds the landed delivery that asked owner (anyone when "") for it.
func EndHeld(q Q, tid int64, owner string) error {
	var o any
	if owner != "" {
		o = owner
	}
	_, err := q.Exec("UPDATE requests SET queue_order = NULL WHERE state = 'landed' AND kind = 'task' AND queue_order IS NOT NULL "+
		"AND (? IS NULL OR target = ?) AND id IN (SELECT request_id FROM task_requests WHERE task_id = ?)", o, o, tid)
	return err
}

func unsend(q Q, tid int64) error {
	_, err := q.Exec("UPDATE tasks SET sent_at = NULL WHERE id = ?", tid)
	return err
}

// MarkTaskDone is db.mark_task_done: true only if this call wrote the first evidence. via is 'sha', 'request' or 'reason'.
func MarkTaskDone(q Q, tid int64, evidence, via string) (bool, error) {
	res, err := q.Exec("UPDATE tasks SET done_evidence = ?, done_at = ?, done_via = ? WHERE id = ? AND done_evidence IS NULL AND discarded_by IS NULL", evidence, Now(), via, tid)
	if err != nil {
		return false, err
	}
	if err := unsend(q, tid); err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	if n > 0 {
		return true, EndHeld(q, tid, "")
	}
	return false, nil
}

// DiscardTask is db.discard_task: true only if this call ended the task.
func DiscardTask(q Q, tid int64, by, reason string) (bool, error) {
	res, err := q.Exec("UPDATE tasks SET discarded_by = ?, discarded_at = ?, discard_reason = ? WHERE id = ? AND "+OpenSQL, by, Now(), reason, tid)
	if err != nil {
		return false, err
	}
	if err := unsend(q, tid); err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	if n > 0 {
		return true, EndHeld(q, tid, "")
	}
	return false, nil
}

// StampTaskDelivered is db.stamp_task_delivered: request rid reached its target's session, so the task it delivers to that
// same owner is delivered from now. Once.
func StampTaskDelivered(q Q, rid int64) error {
	_, err := q.Exec("UPDATE tasks SET delivered_at = ? WHERE delivered_at IS NULL AND owner = "+
		"(SELECT target FROM requests WHERE id = ? AND (kind = 'task' OR (target = ? AND COALESCE(kind, '') NOT IN ('say', 'report')))) AND id IN "+
		"(SELECT task_id FROM task_requests WHERE request_id = ?)", Now(), rid, Human, rid)
	return err
}

// SetTaskQueued is db.set_task_queued: approve a task to run (by set) or send it back to draft (""), true only if it changed.
func SetTaskQueued(q Q, tid int64, by string) (bool, error) {
	var res sql.Result
	var err error
	if by != "" {
		res, err = q.Exec("UPDATE tasks SET queued_by = ?, queued_at = ? WHERE id = ? AND "+OpenSQL+" AND queued_by IS NULL", by, Now(), tid)
	} else {
		res, err = q.Exec("UPDATE tasks SET queued_by = NULL, queued_at = NULL WHERE id = ? AND "+OpenSQL+" AND queued_by IS NOT NULL", tid)
	}
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// AddBlock is db.add_block.
func AddBlock(q Q, tid, blocksOn int64) error {
	_, err := q.Exec("INSERT OR IGNORE INTO task_blocks(task_id, blocks_on, created_at) VALUES (?, ?, ?)", tid, blocksOn, Now())
	return err
}

// RemoveBlock is db.remove_block.
func RemoveBlock(q Q, tid, blocksOn int64) (bool, error) {
	res, err := q.Exec("DELETE FROM task_blocks WHERE task_id = ? AND blocks_on = ?", tid, blocksOn)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// BlocksOnIDs is db.blocks_on_ids: the raw edges the task itself names.
func BlocksOnIDs(q Q, tid int64) ([]int64, error) {
	return ids(q, "SELECT blocks_on FROM task_blocks WHERE task_id = ?", tid)
}

// TasksBlockedBy is db.tasks_blocked_by: every task with a block row naming tid, closed or not.
func TasksBlockedBy(q Q, tid int64) ([]int64, error) {
	return ids(q, "SELECT task_id FROM task_blocks WHERE blocks_on = ? ORDER BY task_id", tid)
}

const decisionCardSQL = "SELECT t.id FROM tasks t WHERE t.owner = ? AND t.discarded_by IS NULL AND t.done_evidence IS NULL AND " +
	"(SELECT MIN(request_id) FROM task_requests WHERE task_id = t.id) = ?"

const openDecisionCardsSQL = "(SELECT MIN(l.request_id) FROM task_requests l JOIN tasks t ON t.id = l.task_id " +
	"WHERE t.owner = ? AND t.discarded_by IS NULL AND t.done_evidence IS NULL GROUP BY t.id)"

// DecisionTaskOfCard is db.decision_task_of_card: the open task of the founder's whose card is request rid, 0 for none.
func DecisionTaskOfCard(q Q, rid int64) (int64, error) {
	var id int64
	err := q.QueryRow(decisionCardSQL, Human, rid).Scan(&id)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return id, err
}

// OpenDecisionCardIDs is db.open_decision_card_ids: the queued cards of the founder's open decision tasks, oldest first; about
// agent (its requester) when given, whose body starts with prefix when given, and is exactly body when hasBody.
func OpenDecisionCardIDs(q Q, agent, prefix string, body *string) ([]int64, error) {
	query := "SELECT id FROM requests WHERE target = ? AND state = 'queued' AND id IN " + openDecisionCardsSQL
	args := []any{Human, Human}
	if agent != "" {
		query += " AND requester = ?"
		args = append(args, agent)
	}
	if prefix != "" {
		query += " AND substr(body, 1, ?) = ?"
		args = append(args, len([]rune(prefix)), prefix)
	}
	if body != nil {
		query += " AND body = ?"
		args = append(args, *body)
	}
	return ids(q, query+" ORDER BY id", args...)
}

// SettleDecisionTasks is db.settle_decision_tasks: a request that is the card of a decision task has left 'queued', and the task
// goes with it: done with the answer as evidence, else discarded.
func SettleDecisionTasks(q Q, rid int64, state, verdict, dismissedBy string) error {
	tid, err := DecisionTaskOfCard(q, rid)
	if err != nil || tid == 0 {
		return err
	}
	if state == "done" && verdict != "" && dismissedBy == "" {
		if _, err := MarkTaskDone(q, tid, verdict, "reason"); err != nil {
			return err
		}
	} else {
		how := state
		if dismissedBy != "" {
			how = "dismissed"
		}
		reason := fmt.Sprintf("its request was %s", how)
		if verdict != "" && dismissedBy == "" {
			reason += ": " + verdict
		}
		if _, err := DiscardTask(q, tid, Human, reason); err != nil {
			return err
		}
	}
	_, err = AddEvent(q, Human, "settle_decision_task", KV{"task_id", tid}, KV{"request_id", rid}, KV{"state", state})
	return err
}

// TaskEdit is what EditTask writes; a nil field is left as it is. TimeoutS, given, always writes, and starts timeout_at from now only when
// the task is doing (IsDoing, the caller's read of that at the time), else leaves it paused.
type TaskEdit struct {
	Owner, Title       *string
	Priority, TimeoutS *int64
	IsDoing            bool
}

// EditTask is db.edit_task: a changed owner, title, priority or timeout, stamped with who and when. The caller has checked permission and
// that something changed; this only writes. A new owner has not had the task yet, so delivered_at and sent_at clear.
func EditTask(q Q, tid int64, by string, ed TaskEdit) error {
	now := Now()
	sets := []string{"edited_by = ?", "edited_at = ?"}
	args := []any{by, now}
	if ed.Owner != nil {
		sets = append(sets, "owner = ?", "delivered_at = NULL", "sent_at = NULL")
		args = append(args, *ed.Owner)
	}
	if ed.Title != nil {
		sets = append(sets, "title = ?")
		args = append(args, *ed.Title)
	}
	if ed.Priority != nil {
		sets = append(sets, "priority = ?")
		args = append(args, *ed.Priority)
	}
	if ed.TimeoutS != nil {
		var at any
		if ed.IsDoing {
			at = now + *ed.TimeoutS
		}
		sets = append(sets, "timeout_s = ?", "timeout_at = ?")
		args = append(args, *ed.TimeoutS, at)
	}
	args = append(args, tid)
	_, err := q.Exec("UPDATE tasks SET "+strings.Join(sets, ", ")+" WHERE id = ?", args...)
	return err
}

// SyncSelfBlocks is db.sync_self_blocks, after tid changed owner from oldOwner: a wait that one agent now holds both ends of goes dormant
// (nobody else could finish it), and a dormant one whose ends differ again counts again. A same-owner wait added on purpose is left as it was.
// It answers the (blocked, blocker) pairs that went dormant.
func SyncSelfBlocks(q Q, tid int64, oldOwner string) ([][2]int64, error) {
	rows, err := q.Query("SELECT b.task_id, b.blocks_on, b.dormant, a.owner, c.owner FROM task_blocks b JOIN tasks a ON a.id = b.task_id "+
		"JOIN tasks c ON c.id = b.blocks_on WHERE b.task_id = ?1 OR b.blocks_on = ?1", tid)
	if err != nil {
		return nil, err
	}
	type wait struct {
		blocked, blocker int64
		dormant          bool
		ao, co           string
	}
	var waits []wait
	for rows.Next() {
		var w wait
		var d int64
		if err := rows.Scan(&w.blocked, &w.blocker, &d, &w.ao, &w.co); err != nil {
			rows.Close()
			return nil, err
		}
		w.dormant = d != 0
		waits = append(waits, w)
	}
	rows.Close()
	var went [][2]int64
	for _, w := range waits {
		other := w.ao
		if w.blocked == tid {
			other = w.co
		}
		switch {
		case w.ao == w.co && other != oldOwner:
			if _, err := q.Exec("UPDATE task_blocks SET dormant = 1 WHERE task_id = ? AND blocks_on = ?", w.blocked, w.blocker); err != nil {
				return nil, err
			}
			went = append(went, [2]int64{w.blocked, w.blocker})
		case w.ao != w.co && w.dormant:
			if _, err := q.Exec("UPDATE task_blocks SET dormant = 0 WHERE task_id = ? AND blocks_on = ?", w.blocked, w.blocker); err != nil {
				return nil, err
			}
		}
	}
	return went, nil
}

// TasksToClock is db.tasks_to_clock: every open task, since whether it is doing is not a column.
func TasksToClock(q Q) ([]*Task, error) {
	return taskRows(q, "SELECT "+taskCols+" FROM tasks WHERE done_evidence IS NULL AND discarded_by IS NULL ORDER BY id")
}

// AdvanceTaskTimeout is db.advance_task_timeout: starts or re-arms a task's clock, timeout_s from now, defaultS for a task never given one.
func AdvanceTaskTimeout(q Q, tid, now, defaultS int64) error {
	_, err := q.Exec("UPDATE tasks SET timeout_s = COALESCE(timeout_s, ?), timeout_at = ? + COALESCE(timeout_s, ?) WHERE id = ?", defaultS, now, defaultS, tid)
	return err
}

// PauseTaskTimeout is db.pause_task_timeout: no deadline runs while a task is not doing.
func PauseTaskTimeout(q Q, tid int64) error {
	_, err := q.Exec("UPDATE tasks SET timeout_at = NULL WHERE id = ? AND timeout_at IS NOT NULL", tid)
	return err
}

// AllTasks is db.all_tasks.
func AllTasks(q Q) ([]*Task, error) {
	return taskRows(q, "SELECT "+taskCols+" FROM tasks ORDER BY priority, id")
}
