package supervisor

import (
	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/structured"
)

// Where a message from chat.send to a structured agent is on its way into the CLI (contract x-delivery). The session's ledger (the
// operations table) has the state of every message it took; a message it has not taken is queued with the daemon until the agent's session is up.

// ChatReceipt is Receipt in the contract: State is queued, sent, confirmed or unknown.
type ChatReceipt struct{ State, UUID string }

// RequestOp is the operation id of the message that tells the agent about request rid.
func RequestOp(rid int64) string { return requestOp(rid) }

// StructuredAgent says whether messages to agent are delivered through a structured session.
func (e *Env) StructuredAgent(agent string) bool { return e.structuredAgent(agent) }

// ReceiptOf is the receipt of the message row to a structured agent. ok is false for a message that has none: a terminal agent's, one the
// person took back, one that was told with others in a batch.
func (e *Env) ReceiptOf(q db.Q, row *db.Request) (r ChatReceipt, ok bool, err error) {
	if !e.structuredAgent(row.Target) || row.State == "withdrawn" {
		return r, false, nil
	}
	op := requestOp(row.ID)
	state, uuid, found, err := db.SendOp(q, row.Target, op)
	if err != nil {
		return r, false, err
	}
	switch {
	case !found && row.State == "queued" && !row.DeliveredAt.Valid:
		return ChatReceipt{string(structured.Queued), structured.UUIDFor(op)}, true, nil
	case !found || state == string(structured.Withdrawn):
		return r, false, nil
	}
	return ChatReceipt{state, uuid}, true, nil
}

// SendChat sends the message row to its structured agent under delivery d and answers the receipt. With no live session nothing is written:
// the receipt is queued and the usual delivery, which starts the session, tells the agent.
func (e *Env) SendChat(q db.Q, row *db.Request, d structured.Delivery) (r ChatReceipt, ok bool, err error) {
	if !e.structuredAgent(row.Target) {
		return r, false, nil
	}
	if e.Agents.Live(row.Target) {
		if _, _, err := e.tellStructured(q, row, d); err != nil {
			return r, false, err
		}
		row, err = db.GetRequest(q, row.ID)
		if err != nil || row == nil {
			return r, false, err
		}
	}
	return e.ReceiptOf(q, row)
}

// WithdrawHeld takes row's message out of the session's queue before the person's cancel closes the row. ok is whether nothing of it was
// written: it was never held or was taken back now. When the session had written it, the row is marked as written and the cancel needs the confirm.
func (e *Env) WithdrawHeld(q db.Q, row *db.Request) (ok bool, err error) {
	defer Catch(&err)
	agent, op := row.Target, requestOp(row.ID)
	if !e.structuredAgent(agent) {
		return true, nil
	}
	state, _, found, err := db.SendOp(q, agent, op)
	check(err)
	switch {
	case !found || state == string(structured.Withdrawn):
		return true, nil
	case state == string(structured.Queued):
		if e.Agents.Live(agent) {
			_, ok, err := e.Agents.Withdraw(agent, op)
			if err != nil {
				return false, err
			}
			if ok {
				check(db.ClearCursor(q, ledgerKey(agent, op)))
				return true, nil
			}
		} else if must(db.SetSendOpState(q, agent, op, string(structured.Queued), string(structured.Withdrawn))) {
			check(db.ClearCursor(q, ledgerKey(agent, op)))
			return true, nil
		}
	}
	e.markWritten(q, row)
	return false, nil
}

// PromoteHeld writes row's held message now. ok is false when it is not held (written already, or taken back); the receipt is then what it is.
func (e *Env) PromoteHeld(q db.Q, row *db.Request) (r ChatReceipt, ok bool, err error) {
	defer Catch(&err)
	agent, op := row.Target, requestOp(row.ID)
	state, _, found, err := db.SendOp(q, agent, op)
	check(err)
	if found && state == string(structured.Queued) && e.Agents.Live(agent) {
		rcpt, err := e.Agents.Promote(agent, op)
		if err != nil {
			return r, false, err
		}
		ok = rcpt.State == structured.Sent || rcpt.State == structured.Confirmed
	}
	if ok {
		check(e.SessionWritten(q, agent, op))
	}
	r, has, err := e.ReceiptOf(q, must(db.GetRequest(q, row.ID)))
	return r, ok && has, err
}

// SessionWritten is what the session layer calls when it writes a message it held: the request is delivered and typed, as when it is sent at once.
func (e *Env) SessionWritten(q db.Q, agent, op string) (err error) {
	defer Catch(&err)
	check(moveLedger(q, ledgerKey(agent, op), sendHeld, sendWritten))
	for _, rid := range ridsOfOp(op) {
		if row := must(db.GetRequest(q, rid)); row != nil {
			e.markWritten(q, row)
		}
	}
	return nil
}

// markWritten marks a request whose message the session has written as delivered and typed.
func (e *Env) markWritten(q db.Q, row *db.Request) {
	if row.State != "queued" {
		return
	}
	must(db.MarkDelivered(q, row.ID))
	check(db.MarkTyped(q, row.ID, e.liveSession(q, row.Target)))
}

// ReopenConfirmed puts a confirmed message of the person's back among the open requests so chat.cancel can close it: a say that the CLI
// echoed is settled as landed, and cancelling a delivered message closes it as rejected and tells the agent to stop. Anything else, a message
// already cancelled included, is left alone. The caller's transaction decides whether the cancel goes ahead.
func (e *Env) ReopenConfirmed(q db.Q, row *db.Request) (reopened bool, err error) {
	defer Catch(&err)
	if row.State != "landed" || row.Kind.String != "say" || row.Requester != db.Human || !e.structuredAgent(row.Target) || must(db.ClosedUnder(q, row.ID)) {
		return false, nil
	}
	state, _, found, err := db.SendOp(q, row.Target, requestOp(row.ID))
	check(err)
	if !found || state != string(structured.Confirmed) {
		return false, nil
	}
	return must(db.ReopenLanded(q, row.ID)), nil
}

// imageLines is the path lines of the images message rid carries, which follow its body when an agent is told of it.
func (e *Env) imageLines(q db.Q, rid int64) string { return attachments.New(e.Home).Lines(q, rid) }
