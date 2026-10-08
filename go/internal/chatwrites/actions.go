package chatwrites

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/supervisor"
)

// The actions the chat page and the mailbox share (handline/actions/requests.py): answer, dismiss, the cancel plan and "seen". Each does its
// writes on the Q it is given (the caller's transaction) and leaves the telling to the Tell it returns, to run once that transaction has committed.

// Result is what an action wrote. OK false is a refusal and Msg its reason; Keep says its writes stand all the same (a request the routing
// denied is kept as a row). Note is what the transaction recorded of the write; Tell runs after the commit and answers the message the caller
// replies with. Request is the message it wrote.
type Result struct {
	OK      bool
	Msg     string // a refusal's reason
	Note    string
	Keep    bool
	Request int64
	Tell    func() string
}

func refusal(msg string) *Result { return &Result{Msg: msg} }

func said(msg string) *Result {
	return &Result{OK: true, Note: msg, Tell: func() string { return msg }}
}

// IsAnswerToHuman is a row the person asked that is no longer queued: what it holds now is the agent's verdict.
func IsAnswerToHuman(r *db.Request) bool { return r.Requester == db.Human && r.State != "queued" }

// WaitsOnPerson is a row the person is to answer: asked of them and still queued, or an answer to something they asked.
func WaitsOnPerson(r *db.Request) bool {
	return (r.Target == db.Human && r.State == "queued") || IsAnswerToHuman(r)
}

// guarded runs what follows a commit: a failure there is logged and the message left empty, since the write is done and the tick is the net.
// With a port missing (the daemon serves the writes before every port has landed) nothing is told.
func guarded(e *supervisor.Env, what string, fn func() (string, error)) (out string) {
	if !e.Wired() {
		return ""
	}
	defer func() {
		if r := recover(); r != nil {
			logf(e, "  %s failed: %v", what, r)
			out = ""
		}
	}()
	out, err := fn()
	if err != nil {
		logf(e, "  %s failed: %v", what, err)
		return ""
	}
	return out
}

func logf(e *supervisor.Env, format string, args ...any) {
	if e.Log != nil {
		e.Log(format, args...)
	}
}

// AnswerWrite is answer_ok: reply to one row of the person's mailbox, whichever way it points. A request to the person is closed with a
// verdict; an answer to something they asked is replied to, as a new request back in that answer's thread. effect is the daemon's record of
// the write, made in the same transaction.
func AnswerWrite(e *supervisor.Env, q db.Q, rid int64, verdict string, effect func(q db.Q, message string) error) (*Result, error) {
	if strings.TrimSpace(verdict) == "" {
		return refusal("nothing sent: no answer typed"), nil
	}
	var note string
	inner := effect
	effect = func(c db.Q, n string) error {
		note = n
		if inner == nil {
			return nil
		}
		return inner(c, n)
	}
	res, err := answerWrite(e, q, rid, verdict, effect)
	if res != nil {
		res.Note = note
	}
	return res, err
}

func answerWrite(e *supervisor.Env, q db.Q, rid int64, verdict string, effect func(q db.Q, message string) error) (*Result, error) {
	row, err := db.GetRequest(q, rid)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return refusal(fmt.Sprintf("#%d is gone", rid)), nil
	}
	if row.State == "queued" && row.Target == db.Human {
		tid, err := db.DecisionTaskOfCard(q, rid)
		if err != nil {
			return nil, err
		}
		if tid != 0 {
			return decide(e, q, tid, verdict, effect)
		}
	}
	if IsAnswerToHuman(row) {
		parent := rid
		if a, err := db.AnswerRowOf(q, rid); err != nil {
			return nil, err
		} else if a != nil {
			parent = a.ID
		}
		// Replied to is read: leaving it in the mailbox would ask the person to dismiss what they have just answered.
		within := func(c db.Q, note string, _ int64) error {
			if err := db.MarkAnnounced(c, []int64{rid}); err != nil {
				return err
			}
			if effect != nil {
				return effect(c, note)
			}
			return nil
		}
		return SayWrite(e, q, db.Human, row.Target, verdict, parent, within)
	}
	r, ok, msg, err := e.ResolveWrite(q, supervisor.ResolveIn{RID: rid, Verdict: verdict, Notify: true, Effect: effect})
	if err != nil {
		return nil, err
	}
	if !ok {
		return refusal(msg), nil
	}
	return &Result{OK: true, Tell: func() string {
		return guarded(e, fmt.Sprintf("telling after the answer to #%d", rid), func() (string, error) { return e.ResolveTell(e.DB, r) })
	}}, nil
}

// decide is tasks.decide: the person's decision on a task they own is the answer to its card. The card's close settles the task (db.SettleDecisionTasks).
func decide(e *supervisor.Env, q db.Q, tid int64, text string, effect func(q db.Q, message string) error) (*Result, error) {
	text = strings.TrimSpace(text)
	t, err := db.GetTask(q, tid)
	if err != nil {
		return nil, err
	}
	switch {
	case t == nil:
		return refusal(fmt.Sprintf("no task T%d", tid)), nil
	case t.Owner != db.Human:
		return refusal(fmt.Sprintf("task T%d is not the founder's to decide", tid)), nil
	case t.Ended():
		return refusal(fmt.Sprintf("task T%d is already decided", tid)), nil
	}
	kids, err := db.OpenChildTasks(q, tid)
	if err != nil {
		return nil, err
	}
	if len(kids) > 0 {
		names := make([]string, len(kids))
		for i, k := range kids {
			names[i] = fmt.Sprintf("T%d", k.ID)
		}
		return refusal(fmt.Sprintf("task T%d still has open child tasks (%s) - finish them first", tid, strings.Join(names, ", "))), nil
	}
	var cardID int64
	if err := q.QueryRow("SELECT COALESCE(MIN(request_id), 0) FROM task_requests WHERE task_id = ?", tid).Scan(&cardID); err != nil {
		return nil, err
	}
	card, err := db.GetRequest(q, cardID)
	if err != nil {
		return nil, err
	}
	if card == nil || card.State != "queued" || card.Target != db.Human {
		// The task is not settled by closing a card, and marking it done is the task verbs' (task.done), not this answer's.
		return refusal(fmt.Sprintf("T%d has no open card to answer: decide it with task.done", tid)), nil
	}
	r, ok, msg, err := e.ResolveWrite(q, supervisor.ResolveIn{RID: card.ID, Verdict: text, Notify: true, Effect: effect})
	if err != nil {
		return nil, err
	}
	if !ok {
		return refusal(msg), nil
	}
	if t, err = db.GetTask(q, tid); err != nil {
		return nil, err
	}
	if !t.Ended() {
		return refusal(fmt.Sprintf("T%d was not settled by its card: decide it with task.done", tid)), nil
	}
	done := fmt.Sprintf("T%d decided", tid)
	return &Result{OK: true, Tell: func() string {
		guarded(e, fmt.Sprintf("telling after the decision on T%d", tid), func() (string, error) { return e.ResolveTell(e.DB, r) })
		return done
	}}, nil
}

// SayWrite is askmod.say as requester: the message is routed and written (a denied one is kept as a row), and settles the request it
// replies to when parent names one. within is more to write in the same transaction, given the message and the new request's id.
func SayWrite(e *supervisor.Env, q db.Q, requester, target, body string, parent int64, within func(q db.Q, note string, rid int64) error) (*Result, error) {
	var note string
	res, settled, err := e.Say(q, supervisor.SayIn{Target: target, Body: body, Requester: requester, ParentID: parent,
		Within: func(c db.Q, n string, rid int64) error {
			note = n
			if within == nil {
				return nil
			}
			return within(c, n, rid)
		}})
	if err != nil {
		return nil, err
	}
	if !res.OK {
		return &Result{Msg: res.Message, Keep: true, Tell: func() string { res.After.Run(); return res.Message }}, nil
	}
	return &Result{OK: true, Note: note, Request: res.RID, Tell: func() string {
		extra := res.After.Run()
		guarded(e, "telling the asker of a settled request", func() (string, error) { return e.SettledTell(e.DB, settled) })
		if e.Wired() {
			e.PushAct(target, res.RID)
		}
		if extra != "" {
			return res.Message + "\n" + extra
		}
		return res.Message
	}}, nil
}

// DismissWrite is dismiss: take one row off the mailbox without replying to it. A still-queued row is closed as dismissed (by me, with
// reason as typed) and carries no answer; a row that already carries a verdict keeps it, and is marked read by whoever asked for it. Read by
// the one who wrote the verdict there is nothing left to do. A refusal by the guard is the only not-OK.
func DismissWrite(e *supervisor.Env, q db.Q, rid int64, me, reason string, tasks []int64, noTask *string, effect func(q db.Q, message string) error) (*Result, error) {
	var note string
	inner := effect
	effect = func(c db.Q, n string) error {
		note = n
		if inner == nil {
			return nil
		}
		return inner(c, n)
	}
	row, err := db.GetRequest(q, rid)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return said(fmt.Sprintf("#%d is gone", rid)), nil
	}
	record := func(msg string) (*Result, error) {
		if effect != nil {
			if err := effect(q, msg); err != nil {
				return nil, err
			}
		}
		return said(msg), nil
	}
	founders := row.State == "queued" && row.Requester == db.Human
	if founders || len(tasks) > 0 {
		refused, err := e.CheckHumanResolve(q, row, tasks, noTask, me, false)
		if err != nil {
			return nil, err
		}
		if refused != "" {
			return refusal(refused), nil
		}
	}
	if row.State != "queued" {
		if me != "" && me == row.Target {
			return record(fmt.Sprintf("#%d was already answered - nothing more for you to do", rid))
		}
		if err := db.MarkAnnounced(q, []int64{rid}); err != nil {
			return nil, err
		}
		return record(fmt.Sprintf("#%d marked read", rid))
	}
	by := me
	if by == "" {
		by = "unknown"
	}
	if founders || len(tasks) > 0 || (noTask != nil && *noTask != "") {
		// Linked before the row is closed: a failure between the two leaves a link on a still-queued row, never an answered row attached to nothing.
		if err := e.RecordHumanResolve(q, rid, tasks, noTask, me); err != nil {
			return nil, err
		}
	}
	// Closed, not announced: a dismissal is not an answer, so nothing is typed at the asker.
	r, ok, msg, err := e.ResolveWrite(q, supervisor.ResolveIn{RID: rid, State: "done", Notify: false, DismissedBy: by, DismissedReason: reason, Effect: effect})
	if err != nil {
		return nil, err
	}
	if !ok {
		return refusal(msg), nil
	}
	requester := row.Requester
	return &Result{OK: true, Note: note, Tell: func() string {
		told := guarded(e, fmt.Sprintf("telling after the dismissal of #%d", rid), func() (string, error) { return e.ResolveTell(e.DB, r) })
		return fmt.Sprintf("%s (dismissed; %s is not told)", told, requester)
	}}, nil
}

// CancelPlan is cancel_plan: what cancelling the request would do and who is told, the sentence a confirm shows before anything is closed.
func CancelPlan(q db.Q, rid int64) (bool, string, error) {
	row, err := db.GetRequest(q, rid)
	if err != nil {
		return false, "", err
	}
	if row == nil {
		return false, fmt.Sprintf("#%d is gone", rid), nil
	}
	if row.State != "queued" {
		return false, fmt.Sprintf("#%d is already %s", rid, row.State), nil
	}
	target, requester := row.Target, row.Requester
	if requester == db.Human && !row.DeliveredAt.Valid {
		return true, fmt.Sprintf("withdraw #%d to %s: not delivered yet, so nobody is told", rid, target), nil
	}
	queued, err := db.QueuedFor(q, target)
	if err != nil {
		return false, "", err
	}
	var behind int64
	for _, r := range queued {
		if r.ID != rid {
			behind = r.ID
			break
		}
	}
	var told []string
	if row.DeliveredAt.Valid {
		told = append(told, target+" is told to stop")
	}
	if requester == db.Human {
		told = append(told, "you are not told")
	} else {
		told = append(told, fmt.Sprintf("%s is told '%s'", requester, supervisor.CancelVerdict))
	}
	way := "queued for"
	if row.DeliveredAt.Valid {
		way = "delivered to"
	}
	out := fmt.Sprintf("cancel #%d (%s %s): closed as rejected, '%s'; %s", rid, way, target, supervisor.CancelVerdict, strings.Join(told, ", "))
	if row.DeliveredAt.Valid && behind != 0 {
		out += fmt.Sprintf("; #%d delivers next", behind)
	}
	return true, out, nil
}

// ForYouSeen records that the person has agent's messages up to id upto in front of them: they are no longer new in FOR YOU. An id, not a
// time: what arrives later has a higher one, so nothing unread is ever hidden.
func ForYouSeen(q db.Q, agent string, upto int64) error {
	key := "for_you_seen:" + agent
	seen, err := db.Cursor(q, key)
	if err != nil {
		return err
	}
	if upto < seen {
		upto = seen
	}
	if err := db.SetCursor(q, key, upto); err != nil {
		return err
	}
	rows, err := q.Query("SELECT id FROM requests WHERE target = ? AND requester = ? AND id <= ? AND read_at IS NULL", db.Human, agent, upto)
	if err != nil {
		return err
	}
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return err
		}
		ids = append(ids, id)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	return db.MarkRead(q, ids)
}
