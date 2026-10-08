package db

import (
	"database/sql"
	"fmt"
	"strings"
)

// Notice and digest names (db.requests).
const (
	NoticeRequester = "task-board"
	DigestHead      = "Result digest - "
	Landed          = "landed"
	// LegacyDismiss is what the old `dismiss` wrote into the verdict before a dismissal was a state.
	LegacyDismiss = "read - no reply"
)

const (
	eventRequest = "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.request_id') END"
	eventTask    = "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END"
)

func onEvent(expr string) string { return strings.Replace(expr, "payload", "e.payload", -1) }

var (
	reasonEvent = "FROM events e WHERE e.kind = 'request_reason' AND " + onEvent(eventRequest) + " = requests.id"
	// closingSQL is what a request closed with: the say that answered it, else the reason it closed without one.
	closingSQL = "COALESCE((SELECT a.body FROM requests a WHERE a.settles = requests.id ORDER BY a.id LIMIT 1), " +
		"(SELECT json_extract(e.payload, '$.reason') " + reasonEvent + " ORDER BY e.id DESC LIMIT 1))"
	answerSQL = closingSQL + " AS answer, (SELECT a.ts FROM requests a WHERE a.settles = requests.id ORDER BY a.id LIMIT 1) AS answered_at"

	dismissedEvent = "FROM events e WHERE e.kind = 'dismissed' AND " + onEvent(eventRequest) + " = requests.id"
	dismissedSQL   = "(SELECT json_extract(e.payload, '$.by') " + dismissedEvent + " ORDER BY e.id DESC LIMIT 1) AS dismisser, " +
		"(SELECT json_extract(e.payload, '$.reason') " + dismissedEvent + " ORDER BY e.id DESC LIMIT 1) AS dismiss_reason"
)

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

// Request is one requests row, with the columns GetRequest adds (Answer, AnsweredAt, Dismisser, DismissReason).
type Request struct {
	ID, TS, ChainDepth                                  int64
	Requester, Target, Body, State                      string
	Files, Relation, Response, Kind, ChainID, TypedSess sql.NullString
	ParentID, DeliveredAt, TypedAt, LandedAt, SendNow   sql.NullInt64
	Notice, ReadAt, StateAt, MootBy, EdgeOK, Settles    sql.NullInt64
	QueueOrder                                          sql.NullFloat64

	Answer, Dismisser, DismissReason sql.NullString
	AnsweredAt                       sql.NullInt64
}

const requestCols = "id, ts, requester, target, body, files, relation, response, kind, chain_depth, parent_id, chain_id, state, queue_order, " +
	"delivered_at, typed_at, typed_session, landed_at, send_now, notice, read_at, state_at, moot_by, edge_ok, settles"

func (r *Request) dest() []any {
	return []any{&r.ID, &r.TS, &r.Requester, &r.Target, &r.Body, &r.Files, &r.Relation, &r.Response, &r.Kind, &r.ChainDepth, &r.ParentID,
		&r.ChainID, &r.State, &r.QueueOrder, &r.DeliveredAt, &r.TypedAt, &r.TypedSess, &r.LandedAt, &r.SendNow, &r.Notice, &r.ReadAt,
		&r.StateAt, &r.MootBy, &r.EdgeOK, &r.Settles}
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
	err := q.QueryRow("SELECT "+requestCols+", "+answerSQL+", "+dismissedSQL+" FROM requests WHERE id = ?", rid).
		Scan(append(r.dest(), &r.Answer, &r.AnsweredAt, &r.Dismisser, &r.DismissReason)...)
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

// AddReason is db.add_reason: why a request closed without an answer.
func AddReason(q Q, rid int64, text string) error {
	_, err := AddEvent(q, "", "request_reason", KV{"request_id", rid}, KV{"reason", text})
	return err
}

// NewRequest is what AddRequest takes; empty strings and zero pointers write NULL.
type NewRequest struct {
	Requester, Target, Body                  string
	Files, Relation, Response, Kind, ChainID string
	ChainDepth                               int64
	ParentID                                 int64  // 0: none
	State                                    string // "": queued
	Verdict                                  string
	Notice                                   bool
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
	landed := (n.Notice || n.Target == Human && n.Kind == "say") && state == "queued"
	if landed || state == "done" {
		state = Landed
	}
	var landedAt, notice, parent any
	if landed {
		landedAt = now
	}
	if n.Notice {
		notice = 1
	}
	if n.ParentID != 0 {
		parent = n.ParentID
	}
	res, err := q.Exec("INSERT INTO requests(ts, requester, target, body, files, relation, response, kind, chain_depth, parent_id, state, chain_id, edge_ok, landed_at, state_at, notice) "+
		"VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
		now, n.Requester, n.Target, n.Body, nullStr(n.Files), nullStr(n.Relation), nullStr(n.Response), nullStr(n.Kind), n.ChainDepth, parent, state,
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
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE chain_id = ? AND state NOT IN ("+marks(len(neverReachedTarget))+") AND settles IS NULL ORDER BY id",
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
		ok, err := CloseIfQueued(q, r.ID, "done", "its task is no longer this agent's to do", CloseOpts{Announced: true, NoSettle: true})
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
	a, err := UnannouncedAnswers(q, agent)
	if err != nil {
		return 0, err
	}
	return len(u) + len(p) + len(a), nil
}

// QueueDepth is db.queue_depth.
func QueueDepth(q Q, target string) (int, error) {
	rows, err := QueuedFor(q, target)
	return len(rows), err
}

// OpenAskIDs is db.open_ask_ids_of: what the agent asked that nobody has answered.
func OpenAskIDs(q Q, requester string) ([]int64, error) {
	return ids(q, "SELECT id FROM requests WHERE requester = ? AND state = 'queued' ORDER BY id", requester)
}

func ids(q Q, query string, args ...any) ([]int64, error) {
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

// UnannouncedAnswers is db.unannounced_answers: what the agent asked, that came back, that it has not been told about.
func UnannouncedAnswers(q Q, agent string) ([]*Request, error) {
	rows, err := q.Query("SELECT "+requestCols+", "+dismissedSQL+", "+answerSQL+" FROM requests WHERE requester = ? "+
		"AND state IN ('landed', 'withdrawn') AND EXISTS (SELECT 1 FROM requests a "+
		"WHERE a.settles = requests.id AND a.state = 'landed' AND a.delivered_at IS NULL) ORDER BY id", agent)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Request
	for rows.Next() {
		r := &Request{}
		if err := rows.Scan(append(r.dest(), &r.Dismisser, &r.DismissReason, &r.Answer, &r.AnsweredAt)...); err != nil {
			return nil, err
		}
		out = append(out, r)
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

// MarkAnnounced is db.mark_announced: the answering says of these requests are shown.
func MarkAnnounced(q Q, rids []int64) error {
	for _, id := range rids {
		if _, err := q.Exec("UPDATE requests SET delivered_at = COALESCE(delivered_at, ?) WHERE settles = ? AND state = 'landed'", Now(), id); err != nil {
			return err
		}
	}
	return nil
}

// PendingNotices is db.pending_notices: the notices the target has not been shown, oldest first.
func PendingNotices(q Q, target string) ([]*Request, error) {
	return requestRows(q, "SELECT "+requestCols+" FROM requests WHERE target = ? AND notice = 1 AND state = 'landed' AND delivered_at IS NULL ORDER BY id", target)
}

// NoticeWaiting is one agent with a notice waiting and when its oldest unshown notice landed.
type NoticeWaiting struct {
	Agent string
	Since int64
}

// AgentsWithNotices is db.agents_with_notices.
func AgentsWithNotices(q Q) ([]NoticeWaiting, error) {
	rows, err := q.Query("SELECT target, MIN(landed_at) FROM requests WHERE notice = 1 AND state = 'landed' AND delivered_at IS NULL AND target != ? GROUP BY target ORDER BY target", Human)
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

// MarkNoticesDigested is db.mark_notices_digested.
func MarkNoticesDigested(q Q, rids []int64) error {
	for _, id := range rids {
		if _, err := q.Exec("UPDATE requests SET delivered_at = COALESCE(delivered_at, ?) WHERE id = ? AND notice = 1 AND state = 'landed'", Now(), id); err != nil {
			return err
		}
	}
	return nil
}

// MarkNoticesShown is db.mark_notices_shown: these notices went out inside a typed delivery to session.
func MarkNoticesShown(q Q, rids []int64, session string) error {
	for _, id := range rids {
		if _, err := q.Exec("UPDATE requests SET delivered_at = COALESCE(delivered_at, ?), typed_at = ?, typed_session = ? WHERE id = ? AND notice = 1 AND state = 'landed'",
			Now(), Now(), nullStr(session), id); err != nil {
			return err
		}
	}
	return nil
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
	return true, nil
}

// ClearDelivered is db.clear_delivered: undo MarkDelivered for a request whose typing failed.
func ClearDelivered(q Q, rid int64) error {
	_, err := q.Exec("UPDATE requests SET delivered_at = NULL WHERE id = ? AND state = 'queued'", rid)
	return err
}

// MarkTyped is db.mark_typed: the send went through into session's terminal; not delivered until MarkLanded.
func MarkTyped(q Q, rid int64, session string) error {
	_, err := q.Exec("UPDATE requests SET typed_at = ?, typed_session = ? WHERE id = ? AND landed_at IS NULL", Now(), nullStr(session), rid)
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
	_, err := q.Exec("UPDATE requests SET state = 'landed', state_at = ? WHERE id = ? AND state = 'queued' AND target != ?", now, rid, Human)
	return err
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

// RetireQueued is db.retire_queued: take a still-queued request out of the queue, delivered or not.
func RetireQueued(q Q, rid int64, state, verdict string) (bool, error) {
	stored, err := StoredState(q, rid, state)
	if err != nil {
		return false, err
	}
	res, err := q.Exec("UPDATE requests SET state = ?, state_at = ?, queue_order = NULL WHERE id = ? AND state = 'queued'", stored, Now(), rid)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return false, nil
	}
	if verdict != "" {
		if err := AddReason(q, rid, verdict); err != nil {
			return false, err
		}
	}
	return true, settle(q, rid, state, verdict, "")
}

// RetireIfUndelivered is db.retire_if_undelivered: a still-queued, undelivered request to a terminal state, atomically.
func RetireIfUndelivered(q Q, rid int64, state, verdict string) (bool, error) {
	stored, err := StoredState(q, rid, state)
	if err != nil {
		return false, err
	}
	res, err := q.Exec("UPDATE requests SET state = ?, state_at = ?, queue_order = NULL WHERE id = ? AND state = 'queued' AND delivered_at IS NULL", stored, Now(), rid)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return false, nil
	}
	if verdict != "" {
		if err := AddReason(q, rid, verdict); err != nil {
			return false, err
		}
	}
	return true, settle(q, rid, state, verdict, "")
}

// CloseOpts are CloseIfQueued's keyword arguments.
type CloseOpts struct {
	Announced       bool   // the answering say is written already told
	DismissedBy     string // a dismissal, by whom
	DismissedReason string
	AnsweredBy      int64 // a reply row that is itself the answer
	NoSettle        bool  // the request closes because its task is being handed on: the decision task stays open
}

// CloseIfQueued is db.close_if_queued: close a request still open, atomically; true only if this call closed it.
func CloseIfQueued(q Q, rid int64, state, verdict string, o CloseOpts) (bool, error) {
	now := Now()
	stored, err := StoredState(q, rid, state)
	if err != nil {
		return false, err
	}
	res, err := q.Exec("UPDATE requests SET state = ?, state_at = ?, queue_order = NULL WHERE id = ? AND state = 'queued'", stored, now, rid)
	if err != nil {
		return false, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return false, nil
	}
	if o.DismissedBy != "" {
		var reason any
		if o.DismissedReason != "" {
			reason = o.DismissedReason
		}
		if _, err := AddEvent(q, o.DismissedBy, "dismissed", KV{"request_id", rid}, KV{"by", o.DismissedBy}, KV{"reason", reason}); err != nil {
			return false, err
		}
	}
	if o.AnsweredBy != 0 {
		if _, err := q.Exec("UPDATE requests SET settles = ? WHERE id = ?", rid, o.AnsweredBy); err != nil {
			return false, err
		}
		if o.Announced { // the reply is what tells, and nobody is to be told: a landed one is shown
			if _, err := q.Exec("UPDATE requests SET delivered_at = COALESCE(delivered_at, ?) WHERE id = ? AND state = 'landed'", now, o.AnsweredBy); err != nil {
				return false, err
			}
		}
	} else if answered, err := o.answers(q, rid, state, verdict); err != nil {
		return false, err
	} else if answered {
		if _, err := AddAnswer(q, rid, verdict, 0, o.Announced); err != nil {
			return false, err
		}
	} else if verdict != "" && o.DismissedBy == "" {
		if err := AddReason(q, rid, verdict); err != nil { // closed without an answer (a task moved on): why
			return false, err
		}
	}
	if o.NoSettle {
		return true, RefreshSentOfRequest(q, rid)
	}
	return true, settle(q, rid, state, verdict, o.DismissedBy)
}

// answers says whether the close writes an answer row: done or taken back, not a dismissal, and not a say closed with nothing said.
func (o CloseOpts) answers(q Q, rid int64, state, verdict string) (bool, error) {
	if (state != "done" && state != "rejected") || o.DismissedBy != "" {
		return false, nil
	}
	if verdict != "" {
		return true, nil
	}
	row, err := GetRequest(q, rid)
	if err != nil || row == nil {
		return false, err
	}
	return row.Kind.String != "say", nil
}

// settle: the founder's decision task follows its request out of the queue.
func settle(q Q, rid int64, state, verdict, dismissedBy string) error {
	if err := RefreshSentOfRequest(q, rid); err != nil {
		return err
	}
	return SettleDecisionTasks(q, rid, state, verdict, dismissedBy)
}

// AddAnswer is db.add_answer: what answered request rid, as a say of its own, born landed. at 0 is now; told says the asker was.
func AddAnswer(q Q, rid int64, text string, at int64, told bool) (int64, error) {
	row, err := GetRequest(q, rid)
	if err != nil {
		return 0, err
	}
	if row == nil {
		return 0, fmt.Errorf("no request #%d to answer", rid)
	}
	when := at
	if when == 0 {
		when = Now()
	}
	var deliveredAt any
	if told {
		deliveredAt = when
	}
	res, err := q.Exec("INSERT INTO requests(ts, requester, target, body, kind, chain_depth, parent_id, settles, chain_id, state, delivered_at, state_at, edge_ok) "+
		"VALUES(?,?,?,?,'say',?,?,?,?,?,?,?,1)",
		when, row.Target, row.Requester, text, row.ChainDepth, rid, rid, row.ChainID, Landed, deliveredAt, when)
	if err != nil {
		return 0, err
	}
	aid, err := res.LastInsertId()
	if err != nil {
		return 0, err
	}
	_, err = q.Exec("UPDATE requests SET queue_order = ? WHERE id = ?", aid, aid)
	return aid, err
}

// AnswerRowOf is db.answer_row_of: the say that settled request rid, nil when none.
func AnswerRowOf(q Q, rid int64) (*Request, error) {
	rows, err := requestRows(q, "SELECT "+requestCols+" FROM requests WHERE settles = ? ORDER BY id LIMIT 1", rid)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return rows[0], nil
}

// AppendVerdict is db.append_verdict: a line on the verdict of a request already closed, before its requester is told.
func AppendVerdict(q Q, rid int64, text string) error {
	_, err := q.Exec("UPDATE requests SET body = body || ? WHERE settles = ?", "\n\n"+text, rid)
	return err
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

// Dismissal is db.dismissal: (dismissed, by, reason) for a row read with GetRequest.
func (r *Request) Dismissal() (bool, string, string) {
	if r.Dismisser.String != "" {
		return true, r.Dismisser.String, r.DismissReason.String
	}
	text := r.Answer.String
	if r.Answer.Valid && text == LegacyDismiss {
		return true, "", ""
	}
	if strings.HasPrefix(text, LegacyDismiss+": ") {
		return true, "", text[len(LegacyDismiss)+2:]
	}
	return false, "", ""
}

// OldestCountedAsk is db.oldest_counted_ask: when the ask that has to leave the window before a slot frees was sent.
func OldestCountedAsk(q Q, requester string, since int64) (int64, bool, error) {
	var ts int64
	err := q.QueryRow("SELECT ts FROM requests WHERE requester = ? AND ts >= ? AND state != 'denied' AND settles IS NULL ORDER BY ts LIMIT 1", requester, since).Scan(&ts)
	if err == sql.ErrNoRows {
		return 0, false, nil
	}
	return ts, err == nil, err
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

// AsksSince is db.asks_since: the asks the requester made since, refused ones and answers not counted.
func AsksSince(q Q, requester string, since int64) (int, error) {
	var n int
	err := q.QueryRow("SELECT COUNT(*) FROM requests WHERE requester = ? AND ts >= ? AND state != 'denied' AND settles IS NULL", requester, since).Scan(&n)
	return n, err
}

// Notice is a say the board wrote for its target in a transaction, to be told after the commit.
type Notice struct {
	RID      int64
	To, Text string
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
