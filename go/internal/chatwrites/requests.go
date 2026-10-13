package chatwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/supervisor"
)

// registerRequests adds what `hl withdraw` and `hl cancel` call.
func (w *writes) registerRequests(s *rpc.Server) {
	s.Register("request.withdraw", rpc.Method{Role: "agent", Write: true, Handler: w.withdrawRequest})
	// Unkept and OwnEvent: a dry run writes and records nothing, and a cancel that happened is judged again by a retry (already closed).
	s.Register("request.cancel", rpc.Method{Role: "founder", Write: true, Unkept: true, OwnEvent: true, Handler: w.cancelRequest})
}

// withdrawRequest is the caller taking back a request of its own before it is delivered. A refusal is a reply, with ok false.
func (w *writes) withdrawRequest(c *rpc.Conn, p map[string]any) (any, error) {
	rid, err := requestParam(p, "id")
	if err != nil {
		return nil, err
	}
	reason := ""
	if v := p["reason"]; v != nil {
		var ok bool
		if reason, ok = v.(string); !ok || strings.TrimSpace(reason) == "" {
			return nil, badParam("reason is text and not empty", "reason")
		}
	}
	after := &supervisor.After{}
	out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		ok, msg, err := w.e.WithdrawWrite(tx, after, rid, c.Caller.Actor(), reason, nil)
		if err != nil {
			return nil, err
		}
		return map[string]any{"ok": ok, "said": msg}, nil
	})
	if err == nil {
		after.Run()
	}
	return out, err
}

// cancelRequest is the founder taking any request out of an agent's queue, delivered or not. With dry_run it answers the plan and writes
// nothing; the call without it is the go-ahead, so a delivered request needs no further confirm.
func (w *writes) cancelRequest(c *rpc.Conn, p map[string]any) (any, error) {
	rid, err := requestParam(p, "id")
	if err != nil {
		return nil, err
	}
	dry := false
	if v := p["dry_run"]; v != nil {
		var ok bool
		if dry, ok = v.(bool); !ok {
			return nil, badParam("dry_run is true or false", "dry_run")
		}
	}
	if dry {
		ok, plan, err := CancelPlan(w.e.DB, rid)
		if err != nil {
			return nil, err
		}
		return map[string]any{"ok": ok, "plan": plan}, nil
	}
	if err := w.withdrawHeld(rid); err != nil {
		return nil, err
	}
	no := func(why string) (any, error) {
		return map[string]any{"ok": false, "said": "not cancelled - " + why}, nil
	}
	tx, err := w.e.DB.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	after := &supervisor.After{}
	if ok, plan, err := CancelPlan(tx, rid); err != nil {
		return nil, err
	} else if !ok {
		return no(plan)
	}
	cp, ok, said, err := w.e.CancelWrite(tx, after, rid, nil, nil)
	if err != nil {
		return nil, err
	}
	if !ok {
		return no(said)
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	after.Run()
	if key, ok := p["request_id"].(string); ok {
		if err := c.RecordWrite("request.cancel", key); err != nil {
			return nil, err
		}
	}
	if cp != nil { // nil is a request of the founder's never delivered: withdrawn, and nobody is told
		said = guarded(w.e, fmt.Sprintf("telling after the cancel of #%d", rid), func() (string, error) { return w.e.CancelTell(w.e.DB, cp) })
	}
	return map[string]any{"ok": true, "said": said}, nil
}
