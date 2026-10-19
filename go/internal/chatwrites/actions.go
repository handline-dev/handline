package chatwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/taskwrites"
)

// The actions the chat page and the mailbox share (handline/actions/requests.py): the founder's decision, a say, the cancel plan and "seen". Each does its
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

// decide is tasks.decide: the person's decision on a task they own closes its card with their words, and is their done on the task.
func decide(e *supervisor.Env, tell taskwrites.Teller, q db.Q, tid int64, text string, effect func(q db.Q, message string) error) (*Result, error) {
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
	card, err := cardOf(q, tid)
	if err != nil {
		return nil, err
	}
	var resolved *supervisor.Resolved
	if card != nil && card.State == "queued" && card.Target == db.Human {
		r, ok, msg, err := e.ResolveWrite(q, supervisor.ResolveIn{RID: card.ID, Verdict: text, Effect: effect})
		if err != nil {
			return nil, err
		}
		if !ok {
			return refusal(msg), nil
		}
		resolved, effect = r, nil
	}
	// The founder's words then go through task.done's rules, which leave a task someone else asked answered, with the founder, and close
	// the founder's own (or one already answered).
	tx, ok := q.(*sql.Tx)
	if !ok || tell == nil {
		return nil, fmt.Errorf("deciding T%d needs the call's transaction and the task teller", tid)
	}
	done, err := taskwrites.MarkDone(tx, e.Home, db.Human, taskwrites.DoneIn{Task: tid, Words: text})
	if err != nil {
		return nil, err
	}
	if effect != nil {
		if err := effect(q, done.Said); err != nil {
			return nil, err
		}
	}
	decided := fmt.Sprintf("T%d decided", tid)
	return &Result{OK: true, Tell: func() string {
		if resolved != nil {
			guarded(e, fmt.Sprintf("telling after the decision on T%d", tid), func() (string, error) { return e.ResolveTell(e.DB, resolved) })
		}
		guarded(e, fmt.Sprintf("telling after T%d was decided", tid), func() (string, error) {
			if done.Reported {
				tell.Reported(tid, done.Owner, done.Told, done.Forget)
			} else {
				tell.Done(tid, db.Human, done.Owner, done.Told, done.Forget, done.Finished)
			}
			return "", nil
		})
		return decided
	}}, nil
}

// cardOf is the request a task was delivered by, nil for none.
func cardOf(q db.Q, tid int64) (*db.Request, error) {
	var id int64
	if err := q.QueryRow("SELECT COALESCE(MIN(request_id), 0) FROM task_requests WHERE task_id = ?", tid).Scan(&id); err != nil {
		return nil, err
	}
	return db.GetRequest(q, id)
}

// DecideCard is task.done's way to decide (taskwrites.CardDecider): the founder's words on a task they own whose card still waits on them
// close the card and the task.
func DecideCard(e *supervisor.Env, tell taskwrites.Teller) taskwrites.CardDecider {
	return func(tx *sql.Tx, tid int64, words string) (bool, string, func(), error) {
		if !e.Wired() { // no card can be resolved before every port has its owner: the task is marked done as before, its card left
			return false, "", nil, nil
		}
		t, err := db.GetTask(tx, tid)
		if err != nil || t == nil || t.Owner != db.Human || t.Ended() {
			return false, "", nil, err
		}
		card, err := cardOf(tx, tid)
		if err != nil || card == nil || card.State != "queued" || card.Target != db.Human {
			return false, "", nil, err
		}
		res, err := decide(e, tell, tx, tid, words, nil)
		if err != nil {
			return false, "", nil, err
		}
		if !res.OK {
			return false, "", nil, stateRefusal(res)
		}
		return true, fmt.Sprintf("T%d decided", tid), func() { res.Tell() }, nil
	}
}

// SayWrite is askmod.say as requester: the message is routed and written (a denied one is kept as a row); parent, when set, links it into
// that message's thread. within is more to write in the same transaction, given the message and the new request's id.
func SayWrite(e *supervisor.Env, q db.Q, requester, target, body string, parent int64, within func(q db.Q, note string, rid int64) error) (*Result, error) {
	return sayWrite(e, q, requester, target, body, parent, within, nil)
}

// sayWrite is SayWrite with act, if set, doing what follows the commit in place of the usual push to the target.
func sayWrite(e *supervisor.Env, q db.Q, requester, target, body string, parent int64, within func(q db.Q, note string, rid int64) error, act func(rid int64)) (*Result, error) {
	var note string
	res, err := e.Say(q, supervisor.SayIn{Target: target, Body: body, Requester: requester, ParentID: parent,
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
		if act != nil {
			act(res.RID)
		} else if e.Wired() {
			e.PushAct(target, res.RID)
		}
		if extra != "" {
			return res.Message + "\n" + extra
		}
		return res.Message
	}}, nil
}

// CancelPlan is cancel_plan: what cancelling the request would do and who is told, the sentence a confirm shows before anything is closed.
func CancelPlan(q db.Q, rid int64) (bool, string, error) {
	row, err := db.GetRequest(q, rid)
	if err != nil {
		return false, "", err
	}
	if row == nil {
		return false, "that message is gone", nil
	}
	if row.State != "queued" {
		return false, fmt.Sprintf("%s is already %s", db.MsgName(q, rid), row.State), nil
	}
	target, requester := row.Target, row.Requester
	if requester == db.Human && !row.DeliveredAt.Valid {
		return true, fmt.Sprintf("withdraw %s to %s: not delivered yet, so nobody is told", db.MsgName(q, rid), target), nil
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
	told := requester + " is not told"
	if requester == db.Human { // the confirm is the founder's to read
		told = "you are not told"
	}
	if row.DeliveredAt.Valid {
		told = target + " is told to stop, " + told
	}
	way := "queued for"
	if row.DeliveredAt.Valid {
		way = "delivered to"
	}
	out := fmt.Sprintf("cancel %s (%s %s): closed as rejected, '%s'; %s", db.MsgName(q, rid), way, target, supervisor.CancelVerdict, told)
	if row.DeliveredAt.Valid && behind != 0 {
		out += fmt.Sprintf("; %s delivers next", db.MsgName(q, behind))
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
