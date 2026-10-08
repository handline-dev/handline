package supervisor

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

// Getting a request or an answer to the agent now, rather than at the next daemon tick (team/ask.py push, daemon/messaging.py).

// Push gets the request to the agent now. rid, when it names a message (anything but a task), is typed at once (TellNow), and so is a task
// marked send-now (a handoff); any other task goes out with the next batch. Everything here is the daemon's own function, so a request
// delivered by the sender and one delivered by the tick are the same delivery; the daemon stays the safety net.
func (e *Env) Push(q db.Q, target string, rid int64) (out string) {
	defer e.guard("telling "+target, &out)
	if target == db.Human {
		return "it is on the founder's screen (`hl`)"
	}
	if !e.registered(target) {
		return fmt.Sprintf("%s is not registered with ocp - nothing can be started for it", target)
	}
	return e.PushHere(q, target, rid)
}

// PushHere is Push without the registration check: the socket verb calls this.
func (e *Env) PushHere(q db.Q, target string, rid int64) (out string) {
	defer e.guard("telling "+target, &out)
	var sent *db.Request
	if rid != 0 {
		sent = must(db.GetRequest(q, rid))
	}
	if sent != nil && (sent.Kind.String != "task" || sent.SendNow.Valid) { // a marked task is a handoff
		told := must(e.TellNow(q, rid))
		if told == "typed" || told == "sent" { // sent: written to a structured agent's session
			return "  delivered to " + target
		}
		return fmt.Sprintf("  %s: %s", target, told)
	}
	var said []string
	row := stateOf(q, target)
	// A structured agent has no terminal; its session is the place it is told.
	if !(e.structuredAgent(target) && e.Agents.Live(target) || row != nil && row.Terminal.String != "") {
		// No row at all is the same case as a row with no terminal: an agent that has never been started has nowhere to be told anything.
		said = append(said, e.Lifecycle.StartForWork(q, target))
		row = stateOf(q, target)
	}
	if row != nil && row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 {
		_, why := e.Lifecycle.WakeAgent(q, target)
		said = append(said, fmt.Sprintf("%s: %s", target, why))
	}
	if sentQ := must(e.DeliverQueued(q, target)); sentQ {
		said = append(said, "delivered to "+target)
	} else if len(said) == 0 {
		why, _ := e.Stuck.DeliveryBlock(q, target)
		if why == "" {
			why = target + " has it; it will read it when it is free"
		}
		said = append(said, why)
	}
	return "  " + joinSaid(said)
}

func joinSaid(said []string) string {
	var out []string
	for _, x := range said {
		if x = strings.TrimSpace(x); x != "" {
			out = append(out, x)
		}
	}
	return strings.Join(out, "; ")
}

// PushAnswer puts the answer in front of whoever asked, now: the other half of Push.
func (e *Env) PushAnswer(q db.Q, requester string) (out string) {
	defer e.guard("telling "+requester, &out)
	if requester == db.Human {
		return "  the founder reads it on `hl`"
	}
	if !e.registered(requester) {
		return fmt.Sprintf("  %s is not registered with ocp - nothing to tell", requester)
	}
	return e.PushAnswerHere(q, requester)
}

// PushAnswerHere is PushAnswer without the registration check.
func (e *Env) PushAnswerHere(q db.Q, requester string) (out string) {
	defer e.guard("telling "+requester, &out)
	row := stateOf(q, requester)
	var said []string
	if row != nil && row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 {
		_, why := e.Lifecycle.WakeAgent(q, requester)
		said = append(said, fmt.Sprintf("%s: %s", requester, why))
	}
	if sentA := must(e.DeliverAnswers(q, requester)); sentA {
		said = append(said, requester+" has been told")
	} else if len(said) == 0 {
		said = append(said, requester+" will be told when it is free")
	}
	return "  " + joinSaid(said)
}
