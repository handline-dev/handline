package chatwrites

import (
	"database/sql"
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
)

// How chat.send reaches a structured agent and what it answers (contract x-delivery): the request_id is the operation, delivery says whether
// the message joins a running turn or waits for its end, and the result carries the receipt.

// deliveryParam is chat.send's delivery word; queue is the default.
func deliveryParam(p map[string]any) (structured.Delivery, error) {
	switch p["delivery"] {
	case nil, "queue":
		return structured.Queue, nil
	case "now":
		return structured.Now, nil
	}
	return "", badParam("delivery is now or queue", "delivery")
}

// sendAct is what follows the commit of a message to a structured agent: it sends the message under d and keeps the receipt in *got. A
// message to an agent with no live session waits for the usual delivery, which starts it.
func (w *writes) sendAct(agent string, d structured.Delivery, got *map[string]any) func(rid int64) {
	return func(rid int64) {
		row, err := db.GetRequest(w.e.DB, rid)
		if err == nil && row != nil {
			var r supervisor.ChatReceipt
			var ok bool
			if r, ok, err = w.e.SendChat(w.e.DB, row, d); ok {
				*got = receiptMap(r)
			}
		}
		if err != nil && w.e.Log != nil {
			w.e.Log("  %s: sending #%d failed: %v", agent, rid, err)
		}
		if w.e.Wired() && !w.e.Agents.Live(agent) {
			w.e.PushAct(agent, rid)
		}
	}
}

func receiptMap(r supervisor.ChatReceipt) map[string]any {
	return map[string]any{"state": r.State, "uuid": r.UUID}
}

// refreshSend makes a repeat of chat.send answer with the receipt as it is now. A message the person took back is not sent again by a retry.
func (w *writes) refreshSend(c *rpc.Conn, reply map[string]any) error {
	n, ok := reply["request"].(float64) // a kept reply is read back as JSON
	if !ok {
		return nil
	}
	rid := int64(n)
	row, err := db.GetRequest(w.e.DB, rid)
	if err != nil || row == nil {
		return err
	}
	if row.State == "withdrawn" && w.e.StructuredAgent(row.Target) {
		return rpc.Refused(fmt.Sprintf("message %d was withdrawn: send it again under a new request_id", rid), "state", "")
	}
	r, has, err := w.e.ReceiptOf(w.e.DB, row)
	if err != nil {
		return err
	}
	if has {
		reply["receipt"] = receiptMap(r)
	} else {
		delete(reply, "receipt")
	}
	return nil
}

// heldMessage is the person's message rid when it is to a structured agent, else nil.
func (w *writes) heldMessage(rid int64) (*db.Request, error) {
	row, err := db.GetRequest(w.e.DB, rid)
	if err != nil || row == nil || row.Requester != db.Human || !w.e.StructuredAgent(row.Target) {
		return nil, err
	}
	return row, nil
}

// withdrawHeld takes rid's message back from the session's queue before chat.cancel closes it. One the session has written is marked
// delivered, so the cancel asks for its confirm.
func (w *writes) withdrawHeld(rid int64) error {
	row, err := w.heldMessage(rid)
	if err != nil || row == nil {
		return err
	}
	_, err = w.e.WithdrawHeld(w.e.DB, row)
	return err
}

// promoteHeld is chat.send_now for a message to a structured agent that the session has recorded: only a queued one is written now, and
// the receipt says it. handled is false for a message the session has not taken, which the usual send_now serves.
func (w *writes) promoteHeld(c *rpc.Conn, rid int64) (out map[string]any, handled bool, err error) {
	row, err := w.heldMessage(rid)
	if err != nil || row == nil {
		return nil, false, err
	}
	state, _, found, err := db.SendOp(w.e.DB, row.Target, supervisor.RequestOp(rid))
	if err != nil || !found {
		return nil, false, err
	}
	notQueued := rpc.Refused(fmt.Sprintf("message %d is not queued: it was written or taken back", rid), "state", "")
	if state != string(structured.Queued) {
		return nil, true, notQueued
	}
	r, ok, err := w.e.PromoteHeld(w.e.DB, row)
	if err != nil {
		return nil, true, err
	}
	if !ok {
		return nil, true, notQueued
	}
	out, err = c.Effect(func(*sql.Tx) (map[string]any, error) {
		return map[string]any{"accepted": true, "said": fmt.Sprintf("message %d is told to %s now", rid, row.Target), "receipt": receiptMap(r)}, nil
	})
	return out, true, err
}
