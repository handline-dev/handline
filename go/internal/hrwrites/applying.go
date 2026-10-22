package hrwrites

import (
	"fmt"
	"slices"
	"strings"
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
	Start func(agent string) (bool, string)
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
	return e.save(prop)
}

// SweepHeld carries each held change one step on; one proposal's failure is logged, not the sweep's.
func (e *Env) SweepHeld(p HeldPorts) error {
	for _, prop := range e.listing() {
		if prop.str("state") != applying {
			continue
		}
		if err := e.carry(prop, p); err != nil {
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
	if ok {
		// An agent the change renamed has no session until it starts, so a failed start is apply_failed, as the contract has it.
		for _, it := range stepsOf(prop) {
			if it.kind() == "rename" && slices.Contains(waiting, it.str("agent")) {
				if started, why := p.Start(it.str("to")); !started {
					ok, said = false, fmt.Sprintf("the change is applied, but %s was not started: %s", it.str("to"), why)
				}
			}
		}
	}
	_, _, err = e.record(prop, ok, said, true)
	return err
}
