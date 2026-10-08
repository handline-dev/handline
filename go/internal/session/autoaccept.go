package session

import (
	"time"

	"github.com/handline/handline/go/internal/term"
)

// Answering the dialogs a working agent meets all day, behind one global setting, on by default: `auto_accept_dialogs` in
// config.yaml, `hl auto-accept`.
//
// Two things are answered. A dialog the founder gave a standing answer (team/dialog_rules) is answered with that rule's option, by
// its number on the screen just read; a dialog no rule matches is left for the founder. The folder-trust screen is lifecycle's
// standing consent and stays on the trust roots, never on a rule. This setting gates both.
//
// accept answers a hook-reported tool call a driver's AcceptsTool takes; no current vendor takes any.

// AutoAcceptKey is the setting that gates both.
const AutoAcceptKey = "auto_accept_dialogs"

// AcceptStuckBackoffS: a prompt that Enter did not clear is left alone this long, not pressed again on every tick.
const AcceptStuckBackoffS = 300

// AutoAcceptEnabled is whether the setting is on.
func (e *Env) AutoAcceptEnabled() bool { return e.Config.Flag(AutoAcceptKey, true) }

// SetAutoAccept turns the setting on or off.
func (e *Env) SetAutoAccept(on bool) error { return e.Config.SetFlag(AutoAcceptKey, on) }

// SweepAutoAccept says yes to each agent's waiting tool call its vendor accepts, and answers each dialog a standing rule covers.
// Only an agent whose hook reported such a tool waiting is looked at for the first, and no screen is read for the second while
// there is no rule, so an idle tick reads nothing.
func (e *Env) SweepAutoAccept() error {
	if !e.AutoAcceptEnabled() {
		return nil
	}
	now := e.now()
	registered := map[string]bool{}
	for _, a := range e.Agents() {
		registered[a] = true
	}
	rules := e.Rules.Titles()
	rows, err := e.AllAgentStates()
	if err != nil {
		return err
	}
	for _, row := range rows {
		if !registered[row.Agent] || row.Terminal == "" {
			continue
		}
		// One agent's unreadable screen is not the tick's failure.
		accepted, err := e.accept(row.Agent, row, now)
		if err == nil && !accepted && len(rules) > 0 {
			_, err = e.answerByRule(row.Agent, row, now, rules)
		}
		if err != nil {
			e.log("  %s: auto-accept failed: %s", row.Agent, err)
		}
	}
	return nil
}

func (e *Env) acceptStuck(agent string, now int64) (bool, error) {
	last, err := e.GetCursor("autoaccept_stuck:" + agent)
	return err == nil && now-last < AcceptStuckBackoffS, err
}

// accept is whether a key was pressed on a waiting tool's prompt.
func (e *Env) accept(agent string, row *AgentRow, now int64) (bool, error) {
	// WaitOf, not the raw file: a wait left by a prompt that was answered without its PostToolUse is stale, and WaitOf is what
	// says so.
	held := e.WaitOf(row)
	tool := ""
	if held != nil {
		tool = held.Tool
	}
	if tool == "" || row.ClosedAt != 0 || row.State != Running {
		return false, nil
	}
	stuck, err := e.acceptStuck(agent, now)
	if err != nil || stuck || e.Held.HeldByOther(agent) != "" { // nothing is pressed at a held terminal
		return false, err
	}
	if !e.Term.AcceptsTool(row.Terminal, tool) {
		return false, nil
	}
	outcome, title, _ := e.Term.AcceptDialog(row.Terminal, tool)
	switch outcome {
	case term.NotPressed:
		return false, nil
	case term.StillOpen:
		if err := e.SetCursor("autoaccept_stuck:"+agent, now); err != nil {
			return true, err
		}
		_ = e.AddEvent(agent, "dialog_accept_failed", map[string]any{"title": title, "tool": tool})
		e.log("  %s: pressed Enter on \"%s\" (%s) and it is still open", agent, title, tool)
		return true, nil
	}
	_ = e.AddEvent(agent, "dialog_accepted", map[string]any{"title": title, "tool": tool})
	e.log("  %s: said yes once to %s (auto-accept)", agent, tool)
	return true, nil
}

// AnswerByRule is a launch's use of the standing rules, for a dialog it meets before the sweep does: true when a rule's option was
// pressed and the dialog closed.
func (e *Env) AnswerByRule(agent, handle string) (bool, error) {
	rules := e.Rules.Titles()
	row, err := e.GetAgentState(agent)
	if err != nil || len(rules) == 0 || row == nil || row.Terminal != handle {
		return false, err
	}
	return e.answerByRule(agent, row, e.now(), rules)
}

// answerByRule presses the option a standing rule names on the dialog this agent shows, then reads the screen again to see it
// close; whether it closed. The cheap read first: only a dialog whose title some rule names is worth the press path's own read.
func (e *Env) answerByRule(agent string, row *AgentRow, now int64, rules map[[2]string]bool) (bool, error) {
	if row.ClosedAt != 0 {
		return false, nil
	}
	if stuck, err := e.acceptStuck(agent, now); err != nil || stuck {
		return false, err
	}
	handle := row.Terminal
	vendor := e.Term.VendorOf(handle)
	blocker := e.Term.ScreenBlockerCached(handle, time.Time{})
	if blocker == nil || blocker.Kind != term.Modal || !rules[[2]string{vendor, blocker.Title}] {
		return false, nil
	}
	chosen := ""
	outcome, title, _ := e.Term.AnswerDialog(handle, func(vendor string, d *term.Dialog) int {
		var numbers []int
		var labels []string
		for _, o := range d.Options {
			numbers, labels = append(numbers, o.Number), append(labels, o.Label)
		}
		n := e.Rules.Match(vendor, d.Title, numbers, labels)
		for _, o := range d.Options {
			if o.Number == n {
				chosen = o.Label
			}
		}
		return n
	})
	if outcome == term.NotPressed {
		return false, nil
	}
	detail := map[string]any{"title": title, "label": chosen, "by": "rule"}
	if outcome == term.StillOpen {
		if err := e.SetCursor("autoaccept_stuck:"+agent, now); err != nil {
			return false, err
		}
		_ = e.AddEvent(agent, "dialog_accept_failed", detail)
		e.log("  %s: pressed '%s' on \"%s\" (rule) and it is still open", agent, chosen, title)
		return false, nil
	}
	_ = e.AddEvent(agent, "dialog_accepted", detail)
	e.log("  %s: answered \"%s\" with '%s' (rule)", agent, title, chosen)
	return true, nil
}
