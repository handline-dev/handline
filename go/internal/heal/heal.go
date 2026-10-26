// Package heal is the daemon getting a stuck agent going again itself, so the founder never sees only "stuck": a hung turn or a message
// that will not deliver is retried, then the agent restarted in a new terminal, then relaunched as a new conversation, one step a while
// apart. Only when all three fail does it raise one My turn card that says what failed and the fix to press.
package heal

import (
	"fmt"
	"strings"
	"sync"

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
	// QuietS is how long an agent must go without trouble before its ladder starts over: one that hangs again soon after a step goes on
	// to the next step, and so reaches the card, rather than being retried from the start for ever.
	QuietS = 2 * 60 * 60
)

// The ladder, one step per rung; cardStep means all three were tried and the card is up.
const (
	retryStep = iota + 1
	restartStep
	relaunchStep
	cardStep
)

var stepNames = map[int64]string{retryStep: "retry", restartStep: "restart in a new terminal", relaunchStep: "relaunch as a new conversation"}

// FixOpenTerminal is the card's action id: open the agent's terminal, since every step the daemon could take was taken.
const FixOpenTerminal = "open_terminal"

// Ports is what the healer does to an agent, all of it the daemon's existing paths.
type Ports interface {
	Agents() []string
	// NeedsPerson is a prompt or dialog waiting on a person: it already has its own card, and a restart would throw the answer away.
	NeedsPerson(agent string) bool
	// LoadHeld is why the machine's load holds the agent's waiting work, "" when it does not: a hold is the reason, not trouble.
	LoadHeld(agent string) string
	// Interrupt presses Esc on the agent's terminal.
	Interrupt(agent string) (ok bool, why string)
	// Push delivers request rid to the agent now, starting or waking it as delivery does.
	Push(q db.Q, agent string, rid int64) string
	// Restart ends the agent's session, without waiting for a hung turn, and starts it in a new terminal: resuming its conversation, or a
	// new one when fresh.
	Restart(agent string, fresh bool) (ok bool, said string, err error)
	// RaiseCard puts a decision in front of the founder; fix is the action id the app turns into a button.
	RaiseCard(q db.Q, agent, text, fix string) error
}

type Healer struct {
	Ports Ports
	Log   func(string)
	// Go runs a step; nil runs it on its own goroutine, so a restart that takes a while never holds up the tick.
	Go func(func())

	mu      sync.Mutex
	running map[string]bool
	steps   sync.WaitGroup // the steps still running on a goroutine of their own (Go nil)
}

func stepKey(agent string) string  { return "heal:" + agent }
func atKey(agent string) string    { return "heal_at:" + agent }
func quietKey(agent string) string { return "heal_quiet:" + agent }

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
	// A task queued behind another is the task queue's; a notice waits for the digest on purpose. A message is typed when the agent is free,
	// and one the load holds waits on purpose too, unless it is the founder's, which the load never holds.
	waiting, err := db.UndeliveredFor(q, agent)
	if err != nil {
		return nil, err
	}
	held := ""
	if len(waiting) > 0 {
		held = h.Ports.LoadHeld(agent)
	}
	for _, r := range waiting {
		if r.Kind.String == "task" || r.Notice.Int64 != 0 || r.EdgeOK.Int64 == 0 || now-r.TS < UndeliveredS || held != "" && r.Requester != db.Human {
			continue
		}
		return &trouble{what: fmt.Sprintf("message %s has not been delivered for %dm", r.Ref, (now-r.TS)/60), rid: r.ID}, nil
	}
	return nil, nil
}

// Sweep takes each stuck agent one step up its ladder, and forgets the ladder of one that has gone QuietS without trouble.
func (h *Healer) Sweep(q db.Q, now int64) error {
	for _, agent := range h.Ports.Agents() {
		if err := h.heal(q, agent, now); err != nil {
			return fmt.Errorf("%s: %w", agent, err)
		}
	}
	return nil
}

func (h *Healer) heal(q db.Q, agent string, now int64) error {
	if h.busy(agent) {
		return nil // its last step is still running
	}
	step, err := db.Cursor(q, stepKey(agent))
	if err != nil {
		return err
	}
	t, err := h.stuck(q, agent, now)
	if err != nil || step == 0 && t == nil {
		return err
	}
	quiet, err := db.Cursor(q, quietKey(agent))
	if err != nil {
		return err
	}
	if t == nil {
		return h.calm(q, agent, step, quiet, now)
	}
	if quiet != 0 { // the trouble is back before the ladder was forgotten: it goes on from where it was
		if err := db.ClearCursor(q, quietKey(agent)); err != nil {
			return err
		}
	}
	if step >= cardStep {
		return nil // the card is up; it is not raised again while the trouble lasts
	}
	if at, err := db.Cursor(q, atKey(agent)); err != nil || (step > 0 && now-at < StepWaitS) {
		return err
	}
	step++
	if err := db.SetCursor(q, stepKey(agent), step); err != nil {
		return err
	}
	if err := db.SetCursor(q, atKey(agent), now); err != nil {
		return err
	}
	if step == cardStep {
		return h.raise(q, agent, t)
	}
	h.start(agent, func() { h.try(q, agent, step, t) })
	return nil
}

// calm is an agent with a ladder and no trouble now: healed is noted once, and the ladder forgotten after QuietS.
func (h *Healer) calm(q db.Q, agent string, step, quiet, now int64) error {
	if quiet == 0 {
		if step < cardStep {
			h.logf("%s: healed after step %d (%s)", agent, step, stepNames[step])
			if _, err := db.AddEvent(q, agent, "healed", db.KV{K: "step", V: step}); err != nil {
				return err
			}
		}
		return db.SetCursor(q, quietKey(agent), now)
	}
	if now-quiet < QuietS {
		return nil
	}
	for _, k := range []string{stepKey(agent), atKey(agent), quietKey(agent)} {
		if err := db.ClearCursor(q, k); err != nil {
			return err
		}
	}
	return nil
}

func (h *Healer) busy(agent string) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.running[agent]
}

func (h *Healer) start(agent string, step func()) {
	h.mu.Lock()
	if h.running == nil {
		h.running = map[string]bool{}
	}
	h.running[agent] = true
	h.mu.Unlock()
	run := func() {
		defer func() {
			h.mu.Lock()
			delete(h.running, agent)
			h.mu.Unlock()
		}()
		step()
	}
	if h.Go != nil {
		h.Go(run)
		return
	}
	h.steps.Add(1)
	go func() {
		defer h.steps.Done()
		run()
	}()
}

// Wait returns once every step started on its own goroutine has ended: the daemon closes the database, and a test its home, only after.
func (h *Healer) Wait() { h.steps.Wait() }

// try runs one step and records it. A retry interrupts a hung turn, or delivers the waiting message again.
func (h *Healer) try(q db.Q, agent string, step int64, t *trouble) {
	var (
		ok   bool
		said string
		err  error
	)
	switch {
	case step == retryStep && t.rid == 0:
		if ok, said = h.Ports.Interrupt(agent); ok {
			said = "interrupted"
		}
	case step == retryStep:
		ok, said = true, strings.TrimSpace(h.Ports.Push(q, agent, t.rid))
	default:
		ok, said, err = h.Ports.Restart(agent, step == relaunchStep)
	}
	if err != nil {
		ok, said = false, err.Error()
	}
	h.logf("%s: %s; step %d (%s): %s", agent, t.what, step, stepNames[step], said)
	if _, err := db.AddEvent(q, agent, "heal_step", db.KV{K: "step", V: step}, db.KV{K: "trouble", V: t.what},
		db.KV{K: "ok", V: ok}, db.KV{K: "said", V: said}); err != nil {
		h.logf("%s: recording step %d failed: %v", agent, step, err)
	}
}

func (h *Healer) raise(q db.Q, agent string, t *trouble) error {
	text := fmt.Sprintf("%s is stuck: %s.", agent, t.what)
	if tid, ok, err := db.CurrentTask(q, agent); err != nil {
		return err
	} else if ok && tid != 0 {
		text = fmt.Sprintf("%s is stuck on T%d: %s.", agent, tid, t.what)
	}
	text += "\n\nThe daemon retried it, restarted it in a new terminal and relaunched it as a new conversation; it is still stuck. " +
		"Open its terminal to see what holds it."
	h.logf("%s: still stuck after every step; raised a card", agent)
	return h.Ports.RaiseCard(q, agent, text, FixOpenTerminal)
}

func (h *Healer) logf(format string, a ...any) {
	if h.Log != nil {
		h.Log("  heal: " + fmt.Sprintf(format, a...))
	}
}
