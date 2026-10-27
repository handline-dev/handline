package hrwrites

import (
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/loadgate"
)

// A change that renames or retires an agent with a session running is not refused: the proposal is held (state applying, with the agents
// in waiting_on), and SweepHeld, run each tick, closes each of those sessions once it is at its prompt, applies the change when none is
// left running, and starts again under its new name each agent the change renamed.

const applying = "applying"

// HeldPorts are the session calls SweepHeld makes.
type HeldPorts struct {
	Alive func(agent string) bool
	// Idle is whether the session is at its prompt: no turn running and no dialog open.
	Idle  func(agent string) bool
	Close func(agent string) (bool, string)
	// Interrupt ends the agent's running turn (Esc); the sweep calls it for a change the founder asked to apply now. Nil: not served.
	Interrupt func(agent string) (bool, string)
	Start     func(agent string) (bool, string)
}

// heldBy are the agents prop renames or retires that have a session running; none when the process table cannot be read, and the apply
// then refuses as before.
func (e *Env) heldBy(prop *Prop) []string {
	if e.Live == nil {
		return nil
	}
	live, ok := e.Live()
	if !ok {
		return nil
	}
	var out []string
	for _, it := range stepsOf(prop) {
		if k, a := it.kind(), it.str("agent"); (k == "rename" || k == "retire") && live[a] && !slices.Contains(out, a) {
			out = append(out, a)
		}
	}
	return out
}

func (e *Env) hold(prop *Prop, busy []string) error {
	prop.set("state", applying)
	prop.set("waiting_on", strings.Join(busy, ","))
	prop.set("waiting_since", time.Now().Unix())
	return e.save(prop)
}

// SweepHeld carries each held change one step on; one proposal's failure is logged, not the sweep's.
func (e *Env) SweepHeld(p HeldPorts) error {
	for _, prop := range e.listing() {
		var err error
		switch {
		case prop.str("state") == applying:
			err = e.carry(prop, p)
		case prop.str("state") == "applied" && prop.str("start_agent") != "":
			err = e.retryStart(prop, p)
		default:
			continue
		}
		if err != nil {
			e.logf("  hr: proposal %s: %v", prop.id(), err)
		}
	}
	return nil
}

func (e *Env) carry(prop *Prop, p HeldPorts) error {
	waiting := strings.Split(prop.str("waiting_on"), ",")
	running, interrupted := false, false
	for _, a := range waiting {
		if a == "" || !p.Alive(a) {
			continue
		}
		running = true
		if !p.Idle(a) {
			if e.interruptDue(prop, p) {
				if ok, said := p.Interrupt(a); ok {
					e.logf("  %s: turn interrupted so proposal %s can be applied now", a, prop.id())
				} else {
					e.logf("  %s: not interrupted for proposal %s: %s", a, prop.id(), said)
				}
				interrupted = true
			}
			continue
		}
		if ok, said := p.Close(a); ok {
			e.logf("  %s: closed so proposal %s can be applied", a, prop.id())
		} else {
			e.logf("  %s: not closed for proposal %s: %s", a, prop.id(), said)
		}
	}
	if interrupted {
		prop.set("apply_now_esc", time.Now().Unix())
		if err := e.save(prop); err != nil {
			return err
		}
	}
	if running {
		return nil // the next tick looks again
	}
	if why := e.unapproved(prop); why != "" {
		_, _, err := e.record(prop, false, why, true)
		return err
	}
	ok, said, err := e.run(prop)
	if err != nil {
		return err
	}
	var unstarted []string
	if ok {
		// An agent the change renamed has no session until it starts. The change itself stands: a start that is held or fails is the
		// agent's follow-up (start_*), not an apply failure.
		for _, it := range stepsOf(prop) {
			if it.kind() == "rename" && slices.Contains(waiting, it.str("agent")) {
				if started, why := p.Start(it.str("to")); !started {
					prop.set("start_agent", it.str("to"))
					prop.set("start_why", why)
					unstarted = append(unstarted, it.str("to"))
				}
			}
		}
	}
	if len(unstarted) > 0 {
		prop.set("start_state", startState(prop.str("start_why")))
	}
	_, _, err = e.record(prop, ok, said, true)
	return err
}

// startState is "held" for a start the machine load put off, which the sweep retries by itself, and "failed" for any other reason.
func startState(why string) string {
	if loadgate.IsHeld(why) {
		return "held"
	}
	return "failed"
}

// retryStart carries an applied change's follow-up on. An agent that is running (the founder forced the start) ends it. Otherwise a held
// start is made once the load allows, and one that fails for another reason turns the follow-up failed, which the sweep leaves to the
// founder's Start now.
func (e *Env) retryStart(prop *Prop, p HeldPorts) error {
	agent := prop.str("start_agent")
	ok, why := p.Alive(agent), ""
	if !ok && prop.str("start_state") == "held" {
		ok, why = p.Start(agent)
	}
	switch {
	case ok:
		prop.pop("start_agent")
		prop.pop("start_state")
		prop.pop("start_why")
	case why != "" && startState(why) == "failed":
		prop.set("start_state", "failed")
		prop.set("start_why", why)
	default:
		return nil // still held: the next tick looks again
	}
	return e.save(prop)
}

// interruptPause is how long a turn is given to end after an Esc before the sweep presses it again: a second Esc soon after the first
// opens Claude Code's rewind menu, which keeps the session from ever being idle.
var interruptPause = 30 * time.Second

// interruptDue is whether an apply-now proposal may have its busy turns interrupted now: once, then again only after interruptPause.
func (e *Env) interruptDue(prop *Prop, p HeldPorts) bool {
	if prop.str("apply_now") == "" || p.Interrupt == nil {
		return false
	}
	last, _ := strconv.ParseInt(prop.str("apply_now_esc"), 10, 64)
	return last == 0 || time.Since(time.Unix(last, 0)) >= interruptPause
}
