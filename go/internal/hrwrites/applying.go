package hrwrites

import (
	"slices"
	"strings"
	"time"
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
		case prop.str("state") == "applied" && prop.str("start_state") == "held":
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
	running := false
	for _, a := range waiting {
		if a == "" || !p.Alive(a) {
			continue
		}
		running = true
		if !p.Idle(a) {
			if prop.str("apply_now") != "" && p.Interrupt != nil {
				if ok, said := p.Interrupt(a); ok {
					e.logf("  %s: turn interrupted so proposal %s can be applied now", a, prop.id())
				} else {
					e.logf("  %s: not interrupted for proposal %s: %s", a, prop.id(), said)
				}
			}
			continue
		}
		if ok, said := p.Close(a); ok {
			e.logf("  %s: closed so proposal %s can be applied", a, prop.id())
		} else {
			e.logf("  %s: not closed for proposal %s: %s", a, prop.id(), said)
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
	if strings.HasPrefix(why, "held:") {
		return "held"
	}
	return "failed"
}

// retryStart carries an applied change's held start on: it starts the agent once the load allows, and a start that fails for another
// reason turns the follow-up failed, which only the founder's Start now retries.
func (e *Env) retryStart(prop *Prop, p HeldPorts) error {
	agent := prop.str("start_agent")
	ok, why := p.Start(agent)
	switch {
	case ok:
		prop.pop("start_agent")
		prop.pop("start_state")
		prop.pop("start_why")
	case startState(why) == "failed":
		prop.set("start_state", "failed")
		prop.set("start_why", why)
	default:
		return nil // still held: the next tick looks again
	}
	return e.save(prop)
}
