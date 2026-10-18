// Package heal is the daemon getting a stuck agent going again itself, so the founder never sees only "stuck": a hung turn or a message
// that will not deliver is retried, then the agent restarted in its terminal, then relaunched as a new conversation, one step a while
// apart. Only when all three fail does it raise one My turn card that says what failed and the fix to press.
package heal

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

const (
	// HungS is how long a turn may run with nothing heard before it counts as hung: the same bound the reconciler gives RUNNING.
	HungS = states.StuckRunningS
	// UndeliveredS is how long a message may wait for an agent that is free to read it.
	UndeliveredS = 600
	// StepWaitS is how long a step is given to work before the next one.
	StepWaitS = 120
)

// The ladder, one step per rung; cardStep means all three were tried and the card is up.
const (
	retryStep = iota + 1
	restartStep
	relaunchStep
	cardStep
)

var stepNames = map[int64]string{retryStep: "retry", restartStep: "restart in its terminal", relaunchStep: "relaunch as a new conversation"}

// FixRestartTerminal is the card's action id: session.restart for the agent, forced.
const FixRestartTerminal = "restart_terminal"

// Ports is what the healer does to an agent, all of it the daemon's existing paths.
type Ports interface {
	Agents() []string
	// NeedsPerson is a prompt or dialog on the agent's screen: it already has its own card, and a restart would throw the answer away.
	NeedsPerson(agent string) bool
	// Interrupt presses Esc on the agent's terminal.
	Interrupt(agent string) (ok bool, why string)
	// Push delivers request rid to the agent now, starting or waking it as delivery does.
	Push(q db.Q, agent string, rid int64) string
	// Restart ends the agent's session and starts it again in its terminal: resuming its conversation, or a new one when fresh.
	Restart(agent string, fresh bool) (ok bool, said string, err error)
	// RaiseCard puts a decision in front of the founder; fix is the action id the app turns into a button.
	RaiseCard(q db.Q, agent, text, fix string) error
}

type Healer struct {
	Ports Ports
	Log   func(string)
}

func stepKey(agent string) string { return "heal:" + agent }
func atKey(agent string) string   { return "heal_at:" + agent }

// trouble is what is wrong with an agent: hung, or a message (rid) it has not been given.
type trouble struct {
	what string
	rid  int64
}

// stuck reads the agent's trouble from its rows, nil when there is none.
func (h *Healer) stuck(q db.Q, agent string, now int64) (*trouble, error) {
	row, err := db.GetAgentState(q, agent)
	if err != nil || row == nil || h.Ports.NeedsPerson(agent) {
		return nil, err
	}
	closed := row.ClosedAt.Valid && row.ClosedAt.Int64 != 0
	switch row.State {
	case states.Running:
		last, ok, err := db.LastActivity(q, agent)
		if err != nil || !ok || closed || row.Terminal.String == "" || now-last < HungS {
			return nil, err
		}
		return &trouble{what: fmt.Sprintf("its turn is hung: nothing heard for %dm", (now-last)/60)}, nil
	case states.Idle, states.Queued, states.Stalled:
	default: // mid-handoff, or waiting for its account on purpose
		return nil, nil
	}
	// A task queued behind another is the task queue's; a notice waits for the digest on purpose. A message is typed when the agent is free.
	waiting, err := db.UndeliveredFor(q, agent)
	if err != nil {
		return nil, err
	}
	for _, r := range waiting {
		if r.Kind.String == "task" || r.Notice.Int64 != 0 || r.EdgeOK.Int64 == 0 || now-r.TS < UndeliveredS {
			continue
		}
		return &trouble{what: fmt.Sprintf("message %s has not been delivered for %dm", r.Ref, (now-r.TS)/60), rid: r.ID}, nil
	}
	return nil, nil
}

// Sweep takes each stuck agent one step up its ladder, and forgets the ladder of one that is no longer stuck.
func (h *Healer) Sweep(q db.Q, now int64) error {
	for _, agent := range h.Ports.Agents() {
		if err := h.heal(q, agent, now); err != nil {
			return fmt.Errorf("%s: %w", agent, err)
		}
	}
	return nil
}

func (h *Healer) heal(q db.Q, agent string, now int64) error {
	step, err := db.Cursor(q, stepKey(agent))
	if err != nil {
		return err
	}
	t, err := h.stuck(q, agent, now)
	if err != nil {
		return err
	}
	if t == nil {
		if step == 0 {
			return nil
		}
		if step < cardStep {
			h.logf("%s: healed after step %d (%s)", agent, step, stepNames[step])
			if _, err := db.AddEvent(q, agent, "healed", db.KV{K: "step", V: step}); err != nil {
				return err
			}
		}
		if err := db.ClearCursor(q, stepKey(agent)); err != nil {
			return err
		}
		return db.ClearCursor(q, atKey(agent))
	}
	if step >= cardStep {
		return nil // the card is up; it is not raised again while the trouble lasts
	}
	if at, err := db.Cursor(q, atKey(agent)); err != nil || (step > 0 && now-at < StepWaitS) {
		return err
	}
	step++
	if step == cardStep {
		if err := h.raise(q, agent, t); err != nil {
			return err
		}
	} else {
		ok, said, err := h.try(q, agent, step, t)
		if err != nil {
			return err
		}
		h.logf("%s: %s; step %d (%s): %s", agent, t.what, step, stepNames[step], said)
		if _, err := db.AddEvent(q, agent, "heal_step", db.KV{K: "step", V: step}, db.KV{K: "trouble", V: t.what},
			db.KV{K: "ok", V: ok}, db.KV{K: "said", V: said}); err != nil {
			return err
		}
	}
	if err := db.SetCursor(q, stepKey(agent), step); err != nil {
		return err
	}
	return db.SetCursor(q, atKey(agent), now)
}

// try runs one step. A retry interrupts a hung turn, or delivers the waiting message again.
func (h *Healer) try(q db.Q, agent string, step int64, t *trouble) (bool, string, error) {
	switch step {
	case retryStep:
		if t.rid == 0 {
			ok, why := h.Ports.Interrupt(agent)
			if ok {
				why = "interrupted"
			}
			return ok, why, nil
		}
		return true, strings.TrimSpace(h.Ports.Push(q, agent, t.rid)), nil
	case restartStep:
		return h.Ports.Restart(agent, false)
	default:
		return h.Ports.Restart(agent, true)
	}
}

func (h *Healer) raise(q db.Q, agent string, t *trouble) error {
	text := fmt.Sprintf("%s is stuck: %s.", agent, t.what)
	if tid, ok, err := db.CurrentTask(q, agent); err != nil {
		return err
	} else if ok && tid != 0 {
		text = fmt.Sprintf("%s is stuck on T%d: %s.", agent, tid, t.what)
	}
	text += "\n\nThe daemon retried, restarted it in its terminal and relaunched it as a new conversation; it is still stuck. " +
		"Restart its terminal, or open it to see what holds it."
	h.logf("%s: still stuck after every step; raised a card", agent)
	return h.Ports.RaiseCard(q, agent, text, FixRestartTerminal)
}

func (h *Healer) logf(format string, a ...any) {
	if h.Log != nil {
		h.Log("  heal: " + fmt.Sprintf(format, a...))
	}
}
