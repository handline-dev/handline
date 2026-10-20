package db

import (
	"database/sql"
	"strings"
)

// Notice and digest names (db.requests).
const (
	NoticeRequester = "task-board"
	DigestHead      = "Result digest - "
	Landed          = "landed"
)

const (
	eventRequest = "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.request_id') END"
	eventTask    = "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END"
)

func onEvent(expr string) string { return strings.Replace(expr, "payload", "e.payload", -1) }

// reasonSQL is what a request closed with: the last reason recorded for it (AddReason).
var reasonSQL = "(SELECT json_extract(e.payload, '$.reason') FROM events e WHERE e.kind = 'request_reason' AND " + onEvent(eventRequest) +
	" = requests.id ORDER BY e.id DESC LIMIT 1) AS reason"

// leftoverDelivery describes a task delivery whose task has ended or gone to someone else: nothing can be done with it, so
// nothing that hands requests out or counts them reads it.
const leftoverDelivery = "(requests.kind = 'task' AND EXISTS (SELECT 1 FROM task_requests l0 WHERE l0.request_id = requests.id) " +
	"AND NOT EXISTS (SELECT 1 FROM task_requests l JOIN tasks t ON t.id = l.task_id " +
	"WHERE l.request_id = requests.id AND t.done_evidence IS NULL AND t.discarded_by IS NULL " +
	"AND t.owner = requests.target))"

// heldDelivery is a task delivery that landed unanswered: the task its target still holds, kept in its queue.
const heldDelivery = "(requests.state = 'landed' AND requests.kind = 'task' AND requests.queue_order IS NOT NULL " +
	"AND EXISTS (SELECT 1 FROM task_requests l JOIN tasks t ON t.id = l.task_id " +
	"WHERE l.request_id = requests.id AND t.owner = requests.target " +
	"AND t.done_evidence IS NULL AND t.discarded_by IS NULL))"

// Request is one requests row, with the Reason GetRequest adds.
type Request struct {
	ID, TS, ChainDepth                                int64
	Requester, Target, Body, State, Ref               string
	Files, Kind, ChainID, TypedSess                   sql.NullString
	ParentID, DeliveredAt, TypedAt, LandedAt, SendNow sql.NullInt64
	Notice, ReadAt, StateAt, MootBy, EdgeOK           sql.NullInt64
	QueueOrder                                        sql.NullFloat64

	Reason sql.NullString
}

const requestCols = "id, ts, requester, target, body, files, kind, chain_depth, parent_id, chain_id, state, queue_order, " +
	"delivered_at, typed_at, typed_session, landed_at, send_now, notice, read_at, state_at, moot_by, edge_ok, ref"

func (r *Request) dest() []any {
	return []any{&r.ID, &r.TS, &r.Requester, &r.Target, &r.Body, &r.Files, &r.Kind, &r.ChainDepth, &r.ParentID,
		&r.ChainID, &r.State, &r.QueueOrder, &r.DeliveredAt, &r.TypedAt, &r.TypedSess, &r.LandedAt, &r.SendNow, &r.Notice, &r.ReadAt,
		&r.StateAt, &r.MootBy, &r.EdgeOK, &r.Ref}
}

func requestRows(q Q, query string, args ...any) ([]*Request, error) {
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Request
	for rows.Next() {
		r := &Request{}
		if err := rows.Scan(r.dest()...); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// GetRequest is db.get_request: nil when there is no such request.
func GetRequest(q Q, rid int64) (*Request, error) {
	r := &Request{}
	err := q.QueryRow("SELECT "+requestCols+", "+reasonSQL+" FROM requests WHERE id = ?", rid).Scan(append(r.dest(), &r.Reason)...)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return r, err
}

// IsStillQueued is db.is_still_queued.
func IsStillQueued(q Q, rid int64) (bool, error) {
	r, err := GetRequest(q, rid)
	return r != nil && r.State == "queued", err
}

// TellsNow is db.tells_now: told ahead of the queue (a say always, anything else once marked send-now).
func (r *Request) TellsNow() bool { return r.Kind.String == "say" || r.SendNow.Valid }

// AddReason is db.add_reason: what a request closed with.
func AddReason(q Q, rid int64, text string) error {
	_, err := AddEvent(q, "", "request_reason", KV{"request_id", rid}, KV{"reason", text})
	return err
}

// NewRequest is what AddRequest takes; empty strings and zero pointers write NULL.
type NewRequest struct {
	Requester, Target, Body string
	Files, Kind, ChainID    string
	ChainDepth              int64
	ParentID                int64  // 0: none
	State                   string // "": queued
	Verdict                 string
	Notice                  bool
	// Memo makes it a notice that is a memo's tell (notice 2): its reader's lead is told when it goes unread an hour (UnreadMemosBefore).
	Memo bool
}

// AddRequest is db.add_request. A notice, or a say to the founder, is born landed. The edge of a request written here is
// checked (edge_ok 1): the Python in-agent-session case, whose requests wait for the daemon's check, is the hl command's, not
// the daemon's.
func AddRequest(q Q, n NewRequest) (int64, error) {
	state := n.State
	if state == "" {
		state = "queued"
	}
	now := Now()
	var landedAt, notice, parent any
	switch {
	case n.Memo:
		notice = 2
	case n.Notice:
		notice = 1
	}
	landed := (notice != nil || n.Target == Human && n.Kind == "say") && state == "queued"
	if landed || state == "done" {
		state = Landed
	}
	if landed {
		landedAt = now
	}
	if n.ParentID != 0 {
		parent = n.ParentID
	}
	res, err := q.Exec("INSERT INTO requests(ts, requester, target, body, files, kind, chain_depth, parent_id, state, chain_id, edge_ok, landed_at, state_at, notice) "+
		"VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
		now, n.Requester, n.Target, n.Body, nullStr(n.Files), nullStr(n.Kind), n.ChainDepth, parent, state,
		nullStr(n.ChainID), 1, landedAt, now, notice)
	if err != nil {
		return 0, err
	}
	rid, err := res.LastInsertId()
	if err != nil {
		return 0, err
	}
	if n.Verdict != "" {
		if err := AddReason(q, rid, n.Verdict); err != nil {
			return 0, err
		}
	}
	if n.ChainID == "" { // a request with no parent starts its own chain, named after itself
		if _, err := q.Exec("UPDATE requests SET chain_id = ? WHERE id = ?", itoa(rid), rid); err != nil {
			return 0, err
		}
	}
	_, err = q.Exec("UPDATE requests SET queue_order = ? WHERE id = ?", rid, rid)
	return rid, err
}

// StoredState is db.stored_state: the state a close with this intent leaves ("done" is answered, so landed; "rejected" is landed
// if it had been delivered, else withdrawn).
func StoredState(q Q, rid int64, intent string) (string, error) {
	switch intent {
	case "done", Landed:
		return Landed, nil
	case "rejected":
		var delivered, landed sql.NullInt64
		err := q.QueryRow("SELECT delivered_at, landed_at FROM requests WHERE id = ?", rid).Scan(&delivered, &landed)
		if err == nil && (delivered.Int64 != 0 || landed.Int64 != 0) {
			return Landed, nil
		}
		if err != nil && err != sql.ErrNoRows {
			return "", err
		}
		return "withdrawn", nil
	}
	return intent, nil
}

// neverReachedTarget are the states a request leaves 'queued' for without reaching its target.
var neverReachedTarget = []string{"denied", "withdrawn", "replaced"}

// ChainMembers is db.chain_members: the asks of a chain that reached their target.
func ChainMembers(q Q, chainID string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE chain_id = ? AND state NOT IN ("+marks(len(neverReachedTarget))+") ORDER BY id",
		kindArgs([]any{chainID}, neverReachedTarget)...)
}

// LeftoverDeliveries is db.leftover_deliveries.
func LeftoverDeliveries(q Q) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE state = 'queued' AND "+leftoverDelivery+" ORDER BY id")
}

// SettleLeftoverDeliveries is db.settle_leftover_deliveries: close quietly every queued delivery whose tasks have all ended.
func SettleLeftoverDeliveries(q Q) ([]int64, error) {
	rows, err := requestRows(q, "SELECT "+requestCols+" FROM requests WHERE state = 'queued' AND kind = 'task' "+
		"AND EXISTS (SELECT 1 FROM task_requests l0 WHERE l0.request_id = requests.id) "+
		"AND NOT EXISTS (SELECT 1 FROM task_requests l JOIN tasks t ON t.id = l.task_id "+
		"WHERE l.request_id = requests.id AND t.done_evidence IS NULL AND t.discarded_by IS NULL) ORDER BY id")
	if err != nil {
		return nil, err
	}
	var done []int64
	for _, r := range rows {
		ok, err := CloseIfQueued(q, r.ID, "done", "its task is no longer this agent's to do")
		if err != nil {
			return nil, err
		}
		if ok {
			done = append(done, r.ID)
		}
	}
	return done, nil
}

// QueuedFor is db.queued_for: what the target has in line, by queue_order.
func QueuedFor(q Q, target string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE target = ? AND (state = 'queued' OR "+heldDelivery+") AND NOT "+leftoverDelivery+" ORDER BY queue_order, id", target)
}

// QueuedAwaiting is the requests queued for target that nothing has closed or landed, oldest first: the rows the stuck findings read.
func QueuedAwaiting(q Q, target string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE state = 'queued' AND NOT "+leftoverDelivery+" AND target = ? ORDER BY ts", target)
}

// UndeliveredFor is db.undelivered_for: the queued requests the target has never been shown.
func UndeliveredFor(q Q, target string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE target = ? AND state = 'queued' AND NOT "+leftoverDelivery+" AND delivered_at IS NULL ORDER BY queue_order, id", target)
}

func firstOf(rows []*Request, err error, keep func(*Request) bool) (*Request, error) {
	if err != nil {
		return nil, err
	}
	var live []*Request
	for _, r := range rows {
		if keep(r) {
			live = append(live, r)
		}
	}
	if len(live) == 0 || live[0].EdgeOK.Int64 == 0 {
		return nil, nil
	}
	return live[0], nil
}

// HeadOfQueue is db.head_of_queue: the one request the target is meant to be working on, or nil. A say is never this. skip is a
// request id to leave out (0: none).
func HeadOfQueue(q Q, target string, skip int64) (*Request, error) {
	rows, err := QueuedFor(q, target)
	return firstOf(rows, err, func(r *Request) bool { return r.Kind.String != "say" && r.ID != skip })
}

// TaskHead is db.task_head: the task delivery to tell next, when its edge has been checked.
func TaskHead(q Q, target string, skip int64) (*Request, error) {
	rows, err := QueuedFor(q, target)
	return firstOf(rows, err, func(r *Request) bool { return r.Kind.String == "task" && r.ID != skip })
}

// HeadOfQueueOrMarked is db.head_of_queue_or_marked.
func HeadOfQueueOrMarked(q Q, target string) (*Request, error) {
	if h, err := HeadOfQueue(q, target, 0); h != nil || err != nil {
		return h, err
	}
	rows, err := SendNowPending(q, target)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return rows[0], nil
}

// ClearEdge is db.clear_edge.
func ClearEdge(q Q, rid int64) error {
	_, err := q.Exec("UPDATE requests SET edge_ok = 1 WHERE id = ?", rid)
	return err
}

// PromoteToFront is db.promote_to_front: ahead of the other undelivered requests queued for its target, never ahead of one delivered.
func PromoteToFront(q Q, rid int64) error {
	var target string
	err := q.QueryRow("SELECT target FROM requests WHERE id = ? AND state = 'queued'", rid).Scan(&target)
	if err == sql.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	var floor sql.NullFloat64
	if err := q.QueryRow("SELECT MIN(queue_order) FROM requests WHERE target = ? AND state = 'queued' AND delivered_at IS NULL", target).Scan(&floor); err != nil {
		return err
	}
	order := float64(rid)
	if floor.Valid {
		order = floor.Float64 - 1
	}
	_, err = q.Exec("UPDATE requests SET queue_order = ? WHERE id = ?", order, rid)
	return err
}

// UnreachedCount is db.unreached_count: what is still owed the agent because it has not reached it.
func UnreachedCount(q Q, agent string) (int, error) {
	u, err := UndeliveredFor(q, agent)
	if err != nil {
		return 0, err
	}
	p, err := ListPending(q, agent)
	if err != nil {
		return 0, err
	}
	return len(u) + len(p), nil
}

// QueueDepth is db.queue_depth.
func QueueDepth(q Q, target string) (int, error) {
	rows, err := QueuedFor(q, target)
	return len(rows), err
}

// OpenAskIDs is db.open_ask_ids_of: what the agent asked that nobody has answered.
func OpenAskIDs(q Q, requester string) ([]int64, error) {
	return IDs(q, "SELECT id FROM requests WHERE requester = ? AND state = 'queued' ORDER BY id", requester)
}

// IDs is the first column of each row as an id.
func IDs(q Q, query string, args ...any) ([]int64, error) {
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

// MarkRead is db.mark_read: the founder has these in front of him, once.
func MarkRead(q Q, rids []int64) error {
	for _, id := range rids {
		if _, err := q.Exec("UPDATE requests SET read_at = COALESCE(read_at, ?) WHERE id = ? AND target = ?", Now(), id, Human); err != nil {
			return err
		}
	}
	return nil
}

// PendingNotices is db.pending_notices: the notices the target has not been shown, oldest first.
func PendingNotices(q Q, target string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE target = ? AND notice >= 1 AND state = 'landed' AND delivered_at IS NULL ORDER BY id", target)
}

// NoticeWaiting is one agent with a notice waiting and when its oldest unshown notice landed.
type NoticeWaiting struct {
	Agent string
	Since int64
}

// AgentsWithNotices is db.agents_with_notices.
func AgentsWithNotices(q Q) ([]NoticeWaiting, error) {
	rows, err := q.Query("SELECT target, MIN(landed_at) FROM requests WHERE notice >= 1 AND state = 'landed' AND delivered_at IS NULL AND target != ? GROUP BY target ORDER BY target", Human)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []NoticeWaiting
	for rows.Next() {
		var n NoticeWaiting
		var since sql.NullInt64
		if err := rows.Scan(&n.Agent, &since); err != nil {
			return nil, err
		}
		n.Since = since.Int64
		out = append(out, n)
	}
	return out, rows.Err()
}

// UnreadMemosBefore is every memo's tell that landed at or before `before` and has been neither shown nor read, oldest first.
func UnreadMemosBefore(q Q, before int64) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE notice = 2 AND state = 'landed' AND delivered_at IS NULL AND read_at IS NULL AND landed_at <= ? ORDER BY id", before)
}

// MarkNoticesDigested is db.mark_notices_digested.
func MarkNoticesDigested(q Q, rids []int64) error {
	for _, id := range rids {
		if _, err := q.Exec("UPDATE requests SET delivered_at = COALESCE(delivered_at, ?) WHERE id = ? AND notice >= 1 AND state = 'landed'", Now(), id); err != nil {
			return err
		}
	}
	return nil
}

// MarkNoticesShown is db.mark_notices_shown: these notices went out inside a typed delivery to session.
func MarkNoticesShown(q Q, rids []int64, session string) error {
	for _, id := range rids {
		if _, err := q.Exec("UPDATE requests SET delivered_at = COALESCE(delivered_at, ?), typed_at = ?, typed_session = ? WHERE id = ? AND notice >= 1 AND state = 'landed'",
			Now(), Now(), nullStr(session), id); err != nil {
			return err
		}
	}
	return nil
}

// MarkFounderAuthored records that the request's words are the founder's own. Only chat.send calls it, and only for a connection whose
// proven role is founder: never from a name, the text, or a requester an agent can write.
func MarkFounderAuthored(q Q, rid int64) error {
	_, err := q.Exec("UPDATE requests SET founder_auth = 1 WHERE id = ?", rid)
	return err
}

// MarkFounderSaid records that a founder's own message is said through the inbox, not typed: its agent could not be typed at.
func MarkFounderSaid(q Q, rid int64) error {
	_, err := q.Exec("UPDATE requests SET founder_auth = 2 WHERE id = ? AND founder_auth = 1", rid)
	return err
}

// FounderAuthored is whether MarkFounderAuthored marked the request and it is typed, not said (MarkFounderSaid).
func FounderAuthored(q Q, rid int64) (bool, error) {
	var v int
	err := q.QueryRow("SELECT founder_auth FROM requests WHERE id = ?", rid).Scan(&v)
	if err == sql.ErrNoRows {
		return false, nil
	}
	return v == 1, err
}

// MarkDelivered is db.mark_delivered: shown to its target, once. False when it was withdrawn or replaced first.
func MarkDelivered(q Q, rid int64) (bool, error) {
	res, err := q.Exec("UPDATE requests SET delivered_at = COALESCE(delivered_at, ?) WHERE id = ? AND state = 'queued'", Now(), rid)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return false, nil
	}
	var target string
	if err := q.QueryRow("SELECT target FROM requests WHERE id = ?", rid).Scan(&target); err != nil {
		return false, err
	}
	if err := SetMeta(q, AgentProgressKey(target), ""); err != nil { // work arrived: the idle spell's progress note ends
		return false, err
	}
	if target == Human { // the founder has no transcript to land in: drawn is delivered
		return true, StampTaskDelivered(q, rid)
	}
	return true, holdDelivered(q, rid, target)
}

// holdDelivered makes the task request rid delivers the agent's current one when nothing it holds is in the way (wayFree): the work it
// was just given is what it is doing.
func holdDelivered(q Q, rid int64, agent string) error {
	if free, err := wayFree(q, agent); err != nil || !free {
		return err
	}
	var tid int64
	err := q.QueryRow("SELECT t.id FROM tasks t JOIN task_requests l ON l.task_id = t.id JOIN requests r ON r.id = l.request_id "+
		"WHERE r.id = ? AND r.kind = 'task' AND t.owner = ? AND t.done_evidence IS NULL AND t.discarded_by IS NULL LIMIT 1", rid, agent).Scan(&tid)
	if err == sql.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	return SetCurrentTask(q, agent, tid, true)
}

// ClearDelivered is db.clear_delivered: undo MarkDelivered for a request whose typing failed.
func ClearDelivered(q Q, rid int64) error {
	_, err := q.Exec("UPDATE requests SET delivered_at = NULL WHERE id = ? AND state = 'queued'", rid)
	return err
}

// MarkTyped is db.mark_typed: the send went through into session's terminal; not delivered until MarkLanded. The CLI's echo can land the
// request before the send records its typing: a landed request is stamped only when it has no typing yet.
func MarkTyped(q Q, rid int64, session string) error {
	_, err := q.Exec("UPDATE requests SET typed_at = ?, typed_session = ? WHERE id = ? AND (landed_at IS NULL OR typed_at IS NULL)", Now(), nullStr(session), rid)
	return err
}

// MarkLanded is db.mark_landed: the receiving session's transcript holds the request.
func MarkLanded(q Q, rid int64) error {
	now := Now()
	if _, err := q.Exec("UPDATE requests SET landed_at = COALESCE(landed_at, ?) WHERE id = ?", now, rid); err != nil {
		return err
	}
	if err := StampTaskDelivered(q, rid); err != nil {
		return err
	}
	if _, err := q.Exec("UPDATE requests SET state = 'landed', state_at = ? WHERE id = ? AND state = 'queued' AND target != ?", now, rid, Human); err != nil {
		return err
	}
	return settleSendLedger(q, rid)
}

// settleSendLedger drops the send ledger entry of each message that carried rid once every request it carried has landed, been read or
// left the queue: the message's fate is known, and an entry kept until its session ends only makes that end report it as unknown.
func settleSendLedger(q Q, rid int64) error {
	rows, err := q.Query("SELECT name FROM cursors WHERE name LIKE 'send:%:request:%' AND "+
		"',' || substr(name, instr(name, ':request:') + 9) || ',' LIKE '%,' || ? || ',%'", rid)
	if err != nil {
		return err
	}
	var names []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			rows.Close()
			return err
		}
		names = append(names, n)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	for _, n := range names {
		ids := "," + n[strings.Index(n, ":request:")+9:] + ","
		var open int
		if err := q.QueryRow("SELECT count(*) FROM requests WHERE ? LIKE '%,' || id || ',%' AND state = 'queued' AND landed_at IS NULL AND read_at IS NULL",
			ids).Scan(&open); err != nil {
			return err
		}
		if open == 0 {
			if _, err := q.Exec("DELETE FROM cursors WHERE name = ?", n); err != nil {
				return err
			}
		}
	}
	return nil
}

// AwaitingLanding is db.awaiting_landing: requests typed at target that its transcript has not shown yet.
func AwaitingLanding(q Q, target string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE target = ? AND state = 'queued' AND typed_at IS NOT NULL AND landed_at IS NULL ORDER BY queue_order, id", target)
}

// OweAgain is db.owe_again: a typed line the transcript never showed is owed again.
func OweAgain(q Q, rid int64) error {
	_, err := q.Exec("UPDATE requests SET delivered_at = NULL, typed_at = NULL, typed_session = NULL WHERE id = ? AND state = 'queued' AND landed_at IS NULL", rid)
	return err
}

// MarkSendNow is db.mark_send_now: to be told as soon as the target can be typed at. False when delivered, closed or missing.
func MarkSendNow(q Q, rid int64) (bool, error) {
	res, err := q.Exec("UPDATE requests SET send_now = COALESCE(send_now, ?) WHERE id = ? AND state = 'queued' AND delivered_at IS NULL", Now(), rid)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// SendNowPending is db.send_now_pending: marked requests for target not yet shown to it, in the order pressed.
func SendNowPending(q Q, target string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE target = ? AND state = 'queued' AND NOT "+leftoverDelivery+
		" AND (send_now IS NOT NULL OR kind = 'say') AND delivered_at IS NULL ORDER BY COALESCE(send_now, ts), id", target)
}

// FounderPending is db.founder_pending: the founder's requests to target not yet shown to it, oldest first.
func FounderPending(q Q, target string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE target = ? AND requester = ? AND NOT "+leftoverDelivery+
		" AND state = 'queued' AND delivered_at IS NULL ORDER BY queue_order, id", target, Human)
}

// FollowupsPending is db.followups_pending: requests to target that reply in the thread of its open head.
func FollowupsPending(q Q, target string, head int64) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE target = ? AND state = 'queued' AND parent_id = ? AND delivered_at IS NULL ORDER BY queue_order, id", target, head)
}

// RetireIfUndelivered is db.retire_if_undelivered: a still-queued, undelivered request to a terminal state, atomically.
func RetireIfUndelivered(q Q, rid int64, state, verdict string) (bool, error) {
	return closeWhere(q, rid, state, verdict, " AND delivered_at IS NULL")
}

// CloseIfQueued is db.close_if_queued (and db.retire_queued): close a request still open, delivered or not, atomically, keeping verdict as
// the reason it closed with; true only if this call closed it.
func CloseIfQueued(q Q, rid int64, state, verdict string) (bool, error) {
	return closeWhere(q, rid, state, verdict, "")
}

// closeWhere closes request rid if it is still queued and matches the condition and.
func closeWhere(q Q, rid int64, state, verdict, and string) (bool, error) {
	stored, err := StoredState(q, rid, state)
	if err != nil {
		return false, err
	}
	res, err := q.Exec("UPDATE requests SET state = ?, state_at = ?, queue_order = NULL WHERE id = ? AND state = 'queued'"+and, stored, Now(), rid)
	if err != nil {
		return false, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return false, nil
	}
	if err := settleSendLedger(q, rid); err != nil {
		return false, err
	}
	if verdict != "" {
		if err := AddReason(q, rid, verdict); err != nil {
			return false, err
		}
	}
	return true, RefreshSentOfRequest(q, rid)
}

// ClosedUnder is db.closed_under: closed out from under its target rather than answered by it.
func ClosedUnder(q Q, rid int64) (bool, error) {
	var one int
	err := q.QueryRow("SELECT 1 FROM events WHERE kind = 'closed_under' AND "+eventRequest+" = ? LIMIT 1", rid).Scan(&one)
	if err == sql.ErrNoRows {
		return false, nil
	}
	return err == nil, err
}

// StandingDenial is db.standing_denial: the id of an identical ask already refused inside the window, 0 for none.
func StandingDenial(q Q, requester, target, body, files string, since int64) (int64, error) {
	var id int64
	err := q.QueryRow("SELECT id FROM requests WHERE requester = ? AND target = ? AND body = ? AND files IS ? AND state = 'denied' AND ts >= ? ORDER BY id DESC LIMIT 1",
		requester, target, body, nullStr(files), since).Scan(&id)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return id, err
}

// Notice is a say the board wrote for its target in a transaction, to be told after the commit.
type Notice struct {
	RID       int64
	To, Text  string
	Unblocked int64 // the task an "unblocked" say is about
}

// InboxFile names the inbox file a request wrote for its target, which goes when the request leaves the queue.
type InboxFile struct {
	Target string
	RID    int64
}

// ReopenLanded puts a landed request back to queued: the one undo of landing, for a message the person may still cancel.
func ReopenLanded(q Q, rid int64) (bool, error) {
	res, err := q.Exec("UPDATE requests SET state = 'queued', state_at = ? WHERE id = ? AND state = 'landed'", Now(), rid)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}
