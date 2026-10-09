package supervisor

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

// Taking a request out of the queue: withdraw, cancel, retire, resolve (team/ask.py). Each is a write on the Q the caller holds (a
// transaction) and a tell to run once it has committed.

// WithdrawWrite takes back your own request before it has been delivered. reason is stored as the row's verdict. effect is the
// daemon's record of it, made in the same transaction. ok false is a refusal and nothing was written.
func (e *Env) WithdrawWrite(q db.Q, after *After, rid int64, requester, reason string, effect func(q db.Q, message string) error) (ok bool, msg string, err error) {
	defer Catch(&err)
	row := must(db.GetRequest(q, rid))
	if row == nil {
		return false, fmt.Sprintf("request %d does not exist", rid), nil
	}
	if row.Requester != requester {
		return false, fmt.Sprintf("#%d is %s -> %s, not yours to withdraw", rid, row.Requester, row.Target), nil
	}
	if row.State != "queued" && !(row.State == "landed" && row.DeliveredAt.Valid) {
		return false, fmt.Sprintf("#%d is already %s - nothing to withdraw", rid, row.State), nil
	}
	release := ""
	if row.Kind.String == "task" {
		if linked := must(db.TasksForRequest(q, rid)); len(linked) > 0 {
			release = fmt.Sprintf(", or `hl task release T%d`", linked[0].ID)
		}
	}
	tooLate := fmt.Sprintf("#%d was already delivered to %s - too late to withdraw; tell %s with a message (`hl say %s \"...\" --parent %d`)%s",
		rid, row.Target, row.Target, row.Target, rid, release)
	if row.DeliveredAt.Valid {
		return false, tooLate, nil
	}
	// Atomic: the write itself is the gate against a delivery landing between the check above and here. Labelled "withdrawn: " so it reads
	// as machine text, not as the target's own answer.
	verdict := ""
	if reason != "" {
		verdict = "withdrawn: " + reason
	}
	said := fmt.Sprintf("request #%d to %s withdrawn", rid, row.Target)
	if !must(db.RetireIfUndelivered(q, rid, "withdrawn", verdict)) {
		return false, tooLate, nil // a delivery landed between the read and the write
	}
	var why any
	if reason != "" {
		why = reason
	}
	must(db.AddEvent(q, requester, "withdraw", db.KV{K: "request_id", V: rid}, db.KV{K: "target", V: row.Target}, db.KV{K: "reason", V: why}))
	if effect != nil {
		if err := effect(q, said); err != nil {
			return false, "", err
		}
	}
	target := row.Target
	after.Add(func() string { e.ForgetInbox(target, rid); return "" })
	return true, said, nil
}

// closeWrite is the write of closeAndTell, alone: false if this call lost the race to close the row.
func (e *Env) closeWrite(q db.Q, rid int64, row *db.Request, verdict string, notify, settle bool) bool {
	if !must(db.CloseIfQueued(q, rid, "rejected", verdict, db.CloseOpts{Announced: !notify, NoSettle: !settle})) {
		return false
	}
	must(db.AddEvent(q, row.Requester, "closed_under", db.KV{K: "request_id", V: rid}, db.KV{K: "target", V: row.Target}))
	return true
}

// closeTell is what follows closeWrite once it has committed: the target told to stop, the requester told, the queue moved up.
func (e *Env) closeTell(q db.Q, rid int64, row *db.Request, verdict string, notify bool) (target, msg string, said []string, err error) {
	defer Catch(&err)
	target = row.Target
	if row.DeliveredAt.Valid {
		typed, why, nerr := e.NotifyCancelled(q, row, verdict)
		if nerr != nil {
			return "", "", nil, nerr
		}
		if typed {
			said = append(said, target+" was told to stop")
		} else {
			said = append(said, fmt.Sprintf("%s could not be told now (%s); resolving #%d is refused to it, so it cannot undo this", target, why, rid))
		}
	}
	msg, err = e.AfterClose(q, rid, row, "rejected", notify)
	return target, msg, said, err
}

// CancelPlan is what CancelWrite leaves for CancelTell.
type CancelPlan struct {
	RID    int64
	Row    *db.Request
	Notify bool
}

// CancelWrite is the founder taking a request out of an agent's queue. WithdrawWrite is the requester's own and stops at delivery; this clears
// a delivered head its agent never resolved. A founder's own request that was never delivered is simply withdrawn (plan nil, message from
// the withdrawal). Anything else is closed as rejected with CancelVerdict, never deleted. guard is asked inside the transaction with the row as
// it stands then and returns a refusal to stop on.
func (e *Env) CancelWrite(q db.Q, after *After, rid int64, effect func(q db.Q, message string) error, guard func(q db.Q, row *db.Request) string) (plan *CancelPlan, ok bool, msg string, err error) {
	defer Catch(&err)
	row := must(db.GetRequest(q, rid))
	if row == nil {
		return nil, false, fmt.Sprintf("request %d does not exist", rid), nil
	}
	if row.State != "queued" {
		stop := ""
		if row.Kind.String == "task" {
			if tasks := must(db.TasksForRequest(q, rid)); len(tasks) > 0 {
				stop = fmt.Sprintf(" - stop its task with `hl task discard T%d` or hand it back", tasks[0].ID)
			}
		}
		return nil, false, fmt.Sprintf("#%d is already %s - nothing to cancel%s", rid, row.State, stop), nil
	}
	if row.Requester == db.Human && !row.DeliveredAt.Valid {
		ok, msg, err := e.WithdrawWrite(q, after, rid, db.Human, "", effect)
		return nil, ok, msg, err
	}
	notify := row.Requester != db.Human
	if guard != nil {
		row = must(db.GetRequest(q, rid))
		if row != nil && row.State == "queued" {
			if stop := guard(q, row); stop != "" {
				return nil, false, stop, nil
			}
		}
	}
	if !e.closeWrite(q, rid, row, CancelVerdict, notify, true) {
		if must(db.IsStillQueued(q, rid)) {
			return nil, false, fmt.Sprintf("#%d was not cancelled - it is still queued", rid), nil
		}
		return nil, false, fmt.Sprintf("#%d was closed while this ran - nothing to cancel", rid), nil
	}
	must(db.AddEvent(q, db.Human, "cancel", db.KV{K: "request_id", V: rid}, db.KV{K: "target", V: row.Target}, db.KV{K: "requester", V: row.Requester},
		db.KV{K: "delivered", V: row.DeliveredAt.Valid && row.DeliveredAt.Int64 != 0}))
	if effect != nil {
		if err := effect(q, fmt.Sprintf("#%d to %s cancelled", rid, row.Target)); err != nil {
			return nil, false, "", err
		}
	}
	return &CancelPlan{rid, row, notify}, true, "", nil
}

// CancelTell is what Cancel says once CancelWrite has committed.
func (e *Env) CancelTell(q db.Q, p *CancelPlan) (string, error) {
	target, msg, said, err := e.closeTell(q, p.RID, p.Row, CancelVerdict, p.Notify)
	if err != nil {
		return "", err
	}
	out := fmt.Sprintf("#%d to %s cancelled", p.RID, target)
	if len(said) > 0 {
		out += " (" + strings.Join(said, "; ") + ")"
	}
	return out + "\n" + msg, nil
}

// RetirePlan is what RetireWrite leaves for RetireTell.
type RetirePlan struct {
	RID     int64
	Row     *db.Request
	Verdict string
	Notify  bool
}

// RetireWrite closes a request still queued, out from under whoever holds it: CancelWrite's close, reached from `hl task release` so it can
// end a task's own open requests the same way. notify nil follows cancel: the requester is told unless it is the founder. settle false
// keeps the decision task open when this request is its card.
func (e *Env) RetireWrite(q db.Q, rid int64, verdict string, notify *bool, settle bool) (plan *RetirePlan, ok bool, why string, err error) {
	defer Catch(&err)
	row := must(db.GetRequest(q, rid))
	if row == nil {
		return nil, false, fmt.Sprintf("#%d is gone", rid), nil
	}
	n := row.Requester != db.Human
	if notify != nil {
		n = *notify
	}
	if row.State != "queued" {
		return nil, false, fmt.Sprintf("#%d is already %s - nothing to retire", rid, row.State), nil
	}
	if !e.closeWrite(q, rid, row, verdict, n, settle) {
		if must(db.IsStillQueued(q, rid)) {
			return nil, false, fmt.Sprintf("#%d was not retired - it is still queued", rid), nil
		}
		return nil, false, fmt.Sprintf("#%d was closed while this ran - nothing to retire", rid), nil
	}
	return &RetirePlan{rid, row, verdict, n}, true, "", nil
}

// RetireTell is what retire_open says once RetireWrite has committed.
func (e *Env) RetireTell(q db.Q, p *RetirePlan) (string, error) {
	target, msg, said, err := e.closeTell(q, p.RID, p.Row, p.Verdict, p.Notify)
	if err != nil {
		return "", err
	}
	out := fmt.Sprintf("#%d to %s retired", p.RID, target)
	if len(said) > 0 {
		out += " (" + strings.Join(said, "; ") + ")"
	}
	return out + "\n" + msg, nil
}

// AfterClose is what follows moving a request out of 'queued', shared by resolve and cancel: the event log, the requester (if told), and
// whatever was behind it in the target's own queue.
func (e *Env) AfterClose(q db.Q, rid int64, row *db.Request, state string, notify bool) (msg string, err error) {
	defer Catch(&err)
	must(db.AddEvent(q, row.Target, "resolve", db.KV{K: "request_id", V: rid}, db.KV{K: "state", V: state}, db.KV{K: "requester", V: row.Requester}))
	// The reply is the wake, for an agent: a requester no agent directory backs has no state to queue. Only queue the requester when nothing else it asked for is still outstanding, and never out of a terminal state or a
	// usage park: an answer is not evidence the window rolled over.
	if row.Response.String == "async" && row.Requester != db.Human && e.registered(row.Requester) && !e.isTerminal(q, row.Requester) && e.wallReason(q, row.Requester, nil) == "" {
		var outstanding int
		check(q.QueryRow("SELECT COUNT(*) c FROM requests WHERE requester = ? AND state = 'queued' AND response = 'async'", row.Requester).Scan(&outstanding))
		if outstanding == 0 {
			// QUEUED only if something is queued or untold: a quiet dismissal leaves neither, and the asker just goes back to idle.
			check(e.Evaluate.QueuedOrIdle(q, row.Requester, fmt.Sprintf("request %d resolved: %s", rid, state)))
		}
	}
	e.ForgetInbox(row.Target, rid)
	said := ""
	if notify {
		said = e.PushAnswer(q, row.Requester)
	}
	// This row may have been what kept the target's current task worth holding.
	if _, _, err := e.AdvanceCurrentTask(q, row.Target); err != nil {
		return "", err
	}
	if must(db.HeadOfQueue(q, row.Target, 0)) != nil {
		if said != "" {
			said += "\n"
		}
		said += e.Push(q, row.Target, 0)
	}
	msg = fmt.Sprintf("request #%d -> %s", rid, state)
	if said != "" {
		msg += "\n" + said
	}
	return msg, nil
}

// ResolveIn is resolve's arguments. Notify false is `dismiss`'s case: settle the row without telling the requester. Settle false closes the
// request without settling the decision task it is the card of (a handoff closes the old holder's request). AnsweredBy is the reply row
// that is itself the answer.
type ResolveIn struct {
	RID                          int64
	Verdict                      string
	State                        string // "": done
	Notify                       bool
	NoSettle                     bool
	DismissedBy, DismissedReason string
	AnsweredBy                   int64
	// OpenTasks opens tasks in the same transaction as the close; their numbers are appended to the verdict.
	OpenTasks func(q db.Q) ([]int64, error)
	// Effect is more to write in the transaction of the close: the daemon's record of it.
	Effect func(q db.Q, message string) error
}

// Resolved is what ResolveWrite leaves for ResolveTell.
type Resolved struct {
	In       ResolveIn
	Row      *db.Request
	Proposal string
	Verdict  string
	State    string
}

// ResolveWrite closes the request; ok false is a refusal and nothing was written (the caller rolls back).
func (e *Env) ResolveWrite(q db.Q, in ResolveIn) (r *Resolved, ok bool, msg string, err error) {
	defer Catch(&err)
	state := in.State
	if state == "" {
		state = "done"
	}
	row := must(db.GetRequest(q, in.RID))
	if row == nil {
		return nil, false, fmt.Sprintf("request %d does not exist", in.RID), nil
	}
	if e.Proposals == nil {
		return nil, false, "", errNoProposals
	}
	proposal := ""
	if row.State == "queued" {
		proposal = e.Proposals.OpenProposalOf(in.RID)
	}
	verdict := in.Verdict
	if in.OpenTasks != nil {
		tids, err := in.OpenTasks(q)
		if err != nil {
			return nil, false, "", err
		}
		names := make([]string, len(tids))
		for i, t := range tids {
			names[i] = fmt.Sprintf("T%d", t)
		}
		verdict += "\n\nOpened as task(s): " + strings.Join(names, ", ")
	}
	// Conditional on 'queued', the same as cancel's close: two answers landing on the same row at once must not have the second overwrite the first.
	closed := must(db.CloseIfQueued(q, in.RID, state, verdict, db.CloseOpts{Announced: !in.Notify, DismissedBy: in.DismissedBy,
		DismissedReason: in.DismissedReason, AnsweredBy: in.AnsweredBy, NoSettle: in.NoSettle}))
	if closed && in.Effect != nil {
		if err := in.Effect(q, fmt.Sprintf("#%d answered", in.RID)); err != nil {
			return nil, false, "", err
		}
	}
	if !closed {
		fresh := must(db.GetRequest(q, in.RID))
		if fresh.State == "queued" {
			return nil, false, fmt.Sprintf("#%d was not resolved - it is still queued", in.RID), nil
		}
		return nil, false, fmt.Sprintf("#%d is already %s - nothing to resolve", in.RID, fresh.State), nil
	}
	return &Resolved{in, row, proposal, verdict, state}, true, "", nil
}

// ResolveTell is what resolve says once ResolveWrite has committed.
func (e *Env) ResolveTell(q db.Q, r *Resolved) (string, error) {
	applied := ""
	if r.Proposal != "" {
		applied = e.Proposals.Answered(q, r.Proposal, r.Row, r.Verdict)
	}
	msg, err := e.AfterClose(q, r.In.RID, r.Row, r.State, r.In.Notify)
	if err != nil {
		return "", err
	}
	if applied != "" {
		return applied + "\n" + msg, nil
	}
	return msg, nil
}

// Resolve is ResolveWrite and ResolveTell in one, on the Q given: on the database each statement commits, as the Python resolve does when it
// has no effect or tasks to open. With either, the caller holds a transaction and uses the halves.
func (e *Env) Resolve(q db.Q, in ResolveIn) (bool, string, error) {
	if in.OpenTasks != nil || in.Effect != nil {
		return false, "", fmt.Errorf("Resolve with OpenTasks or Effect needs a transaction: use ResolveWrite and ResolveTell")
	}
	r, ok, msg, err := e.ResolveWrite(q, in)
	if err != nil || !ok {
		return false, msg, err
	}
	msg, err = e.ResolveTell(q, r)
	return err == nil, msg, err
}
