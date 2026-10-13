package reads

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/states"
)

// Delivery is what the supervisor and the lifecycle know of an agent that the database does not hold. nil is none: a reason that
// needs it is then left out.
type Delivery interface {
	// WallReason is why typing at the agent would only spend quota, "" for none.
	WallReason(q db.Q, agent string, row *db.AgentState) (string, error)
	// ClosedWakeCause is what the next tick wakes a closed agent for, "" for nothing.
	ClosedWakeCause(agent string, row *db.AgentState) (string, error)
	// AccountFull is the agent's account and its short-window percent, when that is at the block.
	AccountFull(agent string) (acct string, pct int, full bool)
	// Wall is what holds back a wake that is due, nil when no wake is due or nothing holds it back.
	Wall(q db.Q, agent string, row *db.AgentState) (*Wall, error)
	// ForgottenResolve is the head request an agent looks to have forgotten (told, still open, nothing asked) and how long it has sat idle.
	ForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) (head, idleS int64, found bool, err error)
	// WakeWaitReason is the sentence for a wake that is due and walled, "" when no wake is due or nothing holds it back.
	WakeWaitReason(q db.Q, agent string, row *db.AgentState) (string, error)
	// Trigger is what the daemon's next tick would decide for the agent on account (hl triggers).
	Trigger(q db.Q, agent string, row *db.AgentState, account string) (Trigger, error)
}

// Trigger is the tick's verdict on an agent. When its triggers do not apply, Fired is false and Reason is the walled wake that holds it,
// "" for none.
type Trigger struct {
	Applies, Fired bool
	Kind, Reason   string
}

// QueueReason is queue_reason: why request rid, queued for agent, has not reached it. It is the one builder behind chat.messages'
// sending_reason and the chat detector's chat.message events.
//
// Straight from _delivery_state, in its order. drops "no terminal", "terminal gone", "no session" -> nothing reaches it (terminal-only:
// a structured agent has no terminal and its closed session is "closed, ..."); drops "closed, no daemon" -> this is the daemon;
// adds "switched with its terminal open" (a structured agent whose row still holds a terminal); drops "closed, starting" -> a structured wake holds an in-process lock, not the terminal lock file.
func (e *Env) QueueReason(agent string, rid int64) (string, error) {
	req, err := db.GetRequest(e.DB, rid)
	if err != nil {
		return "", err
	}
	if req != nil && req.TellsNow() {
		return e.deliveryState(agent, "sending now")
	}
	if head, err := db.HeadOfQueue(e.DB, agent, 0); err != nil {
		return "", err
	} else if head != nil && head.ID == rid {
		if blocked, err := db.HeadTaskBlocked(e.DB, agent, head); err != nil || blocked {
			return "its own task is blocked", err
		}
	}
	head, err := db.EffectiveHead(e.DB, agent)
	if err != nil {
		return "", err
	}
	if head != nil && head.ID != rid {
		return fmt.Sprintf("behind #%d", head.ID), nil
	}
	return e.deliveryState(agent, "waiting for the agent")
}

// deliveryState is _delivery_state's short phrase, or none when nothing holds the work back.
func (e *Env) deliveryState(agent, none string) (string, error) {
	if !contains(paths.ListAgents(e.Home), agent) {
		return "not registered", nil
	}
	row, err := db.GetAgentState(e.DB, agent)
	if err != nil {
		return "", err
	}
	if row == nil {
		return "never started", nil
	}
	if row.State == states.Stalled {
		return "stalled", nil
	}
	closed := row.ClosedAt.Valid && row.ClosedAt.Int64 != 0
	if !closed && row.Terminal.String != "" && e.Structured != nil && e.Structured(agent) {
		return "switched with its terminal open", nil
	}
	if closed {
		return e.closedState(agent, row)
	}
	switch row.State {
	case states.Running:
		return "mid-turn", nil
	case states.Handoff:
		return "mid-handoff", nil
	}
	if e.Delivery != nil {
		why, err := e.Delivery.WallReason(e.DB, agent, row)
		if err != nil {
			return "", err
		}
		if why != "" {
			return "account at the wall", nil
		}
	}
	return none, nil
}

func (e *Env) closedState(agent string, row *db.AgentState) (string, error) {
	if e.Delivery != nil {
		if _, _, full := e.Delivery.AccountFull(agent); full {
			return "closed, account full", nil
		}
		cause, err := e.Delivery.ClosedWakeCause(agent, row)
		if err != nil {
			return "", err
		}
		if cause != "" {
			return "closed, waking", nil
		}
	}
	head, err := db.EffectiveHead(e.DB, agent)
	if err != nil {
		return "", err
	}
	if head != nil && head.DeliveredAt.Valid && head.DeliveredAt.Int64 != 0 {
		return "closed, holding", nil
	}
	return "closed, nothing to wake it", nil
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}
