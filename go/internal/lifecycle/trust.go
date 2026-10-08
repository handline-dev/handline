package lifecycle

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/term"
)

// clearTrustPrompt is the wake paths' gate before the wake prompt: a no-op when nothing is showing, else standing consent or a
// decision. It runs ahead of typeable_now, which refuses this screen as a dialog and would leave it unanswered. The deliver paths only
// check typeable_now, so they wait and the blocked sweep reports it. launched: this wake has just started the session, so any other
// dialog is a start-up one; without it the session may be mid-work, and its dialogs (a tool's permission prompt) are the hooks' to
// report, not a stall.
func (m *Manager) clearTrustPrompt(agent, handle string, launched bool) (bool, string) {
	// One driver scope for the whole check: the reads and the poll all ask this handle's driver.
	defer m.Term.DriverScope()()
	// An agent on a config directory that has never run it can meet both screens, in either order, and whichever is left standing would
	// take the wake's Enter.
	for i := 0; i < MaxStartupScreens; i++ {
		var ok bool
		var why string
		switch {
		case m.Term.BypassPromptShowing(handle):
			ok, why = m.handleBypassPrompt(agent, handle)
		case m.Term.TrustPromptShowing(handle):
			ok, why = m.handleTrustPrompt(agent, handle)
		default:
			if !launched {
				return true, "clear"
			}
			handled, hok, hwhy := m.handleStartupDialog(agent, handle)
			if !handled {
				return true, "clear"
			}
			ok, why = hok, hwhy
		}
		if !ok {
			return false, why
		}
	}
	return false, fmt.Sprintf("%s's terminal still shows a startup screen after %d answers", agent, MaxStartupScreens)
}

// bypassGrant is whether the founder let this agent run with permissions skipped - configured and granted - and why not. The launch that
// raised the warning was made on that word, so accepting it is the same word. An agent.yaml that cannot be read allows nothing.
func (m *Manager) bypassGrant(agent string) (bool, string) {
	granted, err := m.Agents.SkipPermissionsInEffect(agent)
	if err != nil {
		return false, "its agent.yaml could not be read (" + err.Error() + ")"
	}
	if granted {
		return true, ""
	}
	return false, "skipping permissions is not granted for it"
}

// handleBypassPrompt answers the vendor's bypass-permissions warning, which a new config directory raises on the first launch with
// skipped permissions. Accepted when this agent may skip them; otherwise nothing is pressed or typed (its cursor starts on the exit
// option) and the wake fails with the reason, which the stall that follows carries to the founder.
func (m *Manager) handleBypassPrompt(agent, handle string) (bool, string) {
	allowed, why := m.bypassGrant(agent)
	if !allowed {
		return false, fmt.Sprintf("%s's terminal is showing the bypass-permissions warning and %s - nothing was typed; answer it in its terminal", agent, why)
	}
	if ok, why := m.Term.AcceptBypass(handle); !ok {
		return false, fmt.Sprintf("%s: the bypass-permissions warning was not accepted: %s", agent, why)
	}
	must(db.AddEvent(m.Q, agent, "bypass_accepted"))
	m.Log("  %s: accepted the bypass-permissions warning (skip permissions is granted)", agent)
	m.Term.WaitIdle(handle)
	return true, "accepted"
}

// handleTrustPrompt is standing consent: press "Yes" only for the folder the dialog itself names, only when that folder is both a
// declared root and the one on record for this agent, and only while `hl auto-accept` is on. Anything else - unreadable, undeclared, a
// mismatch with the recorded worktree, or the setting off - raises a decision task, as does a press whose keys did not land.
func (m *Manager) handleTrustPrompt(agent, handle string) (bool, string) {
	shown, recorded, consented := m.trustConsent(agent, handle)
	if !consented {
		return m.raiseTrustPrompt(agent, handle, shown, recorded)
	}
	return m.pressTrust(agent, handle, shown, recorded)
}

// AutoBypass is the blocked sweep's answer to the warning a wake never met: accepted when the agent may skip permissions, left alone
// (and reported by the sweep) when not. True when it went.
func (m *Manager) AutoBypass(agent string) (went bool, err error) {
	defer catch(&err)
	handle := must(db.Terminal(m.Q, agent))
	if allowed, _ := m.bypassGrant(agent); handle == "" || !allowed {
		return false, nil
	}
	defer m.Term.DriverScope()()
	if !m.Term.BypassPromptShowing(handle) {
		return false, nil
	}
	ok, _ := m.handleBypassPrompt(agent, handle)
	return ok, nil
}

func samePath(a, b string) bool { return realpath(expandUser(a)) == realpath(expandUser(b)) }

// trustConsent is (folder on screen, folder on record, whether standing consent covers it): the auto-accept setting is on and the
// folder is a declared root and the agent's own recorded worktree.
func (m *Manager) trustConsent(agent, handle string) (shown, recorded string, consented bool) {
	if row := must(db.GetAgentState(m.Q, agent)); row != nil {
		recorded = m.Spawn.WorktreePath(row.Worktree.String)
	}
	shown = m.Term.TrustPromptFolder(handle)
	consented = m.Blocked.AutoAcceptEnabled() && shown != "" && recorded != "" && m.Agents.IsDeclared(shown) && samePath(shown, recorded)
	return
}

// AutoTrust is the blocked sweep's use of standing consent, for a trust screen a wake never met: press "Yes" when consent covers it,
// and say nothing otherwise - the sweep reports what it cannot clear. True when the screen went.
func (m *Manager) AutoTrust(agent string) (went bool, err error) {
	defer catch(&err)
	handle := must(db.Terminal(m.Q, agent))
	if handle == "" {
		return false, nil
	}
	defer m.Term.DriverScope()()
	if !m.Term.TrustPromptShowing(handle) {
		return false, nil
	}
	shown, recorded, consented := m.trustConsent(agent, handle)
	if !consented {
		return false, nil
	}
	ok, _ := m.pressTrust(agent, handle, shown, recorded)
	return ok, nil
}

func (m *Manager) pressTrust(agent, handle, shown, recorded string) (bool, string) {
	ok, _ := m.Term.ConfirmTrust(handle)
	// The redraw is not instant; checking on the very next line risks reading the screen the vendor was still drawing, and raising a
	// decision for a trust that actually went through.
	m.Term.WaitForTrustPrompt(handle, false)
	if !ok || m.Term.TrustPromptShowing(handle) {
		return m.raiseTrustPrompt(agent, handle, shown, recorded)
	}
	must(db.AddEvent(m.Q, agent, "trust_confirmed", db.KV{K: "path", V: shown}))
	m.Log("  %s: trusted %s (standing consent)", agent, shown)
	// The screen just changed under us; typing the wake prompt before the vendor has finished drawing its ordinary TUI loses the
	// opening characters.
	m.Term.WaitIdle(handle)
	return true, "trusted"
}

// handleStartupDialog is any other dialog a launch stops at - the vendor's screens change, and each one that was not named left the
// wake failing until it read FATAL with no word of what was on screen. handled is false when there is none. A standing rule's option is
// pressed; otherwise nothing is pressed and the founder is shown the dialog in its own words.
func (m *Manager) handleStartupDialog(agent, handle string) (handled, ok bool, why string) {
	seen := m.Term.ScreenBlocker(handle)
	if seen == nil || seen.Kind != term.Modal {
		return false, false, ""
	}
	if m.Blocked.AutoAcceptEnabled() && m.Blocked.AnswerByRule(m.Q, agent, handle) {
		return true, true, "answered by a standing rule"
	}
	return true, false, m.raiseDialogStall(agent, seen)
}

// raiseDialogStall: STALLED, with one decision for the founder, until the dialog is gone: the same shape as the trust stall, which the
// sweep lifts when the screen is read clear.
func (m *Manager) raiseDialogStall(agent string, seen *term.Blocker) string {
	if row := must(db.GetAgentState(m.Q, agent)); row != nil && row.State == states.Stalled && strings.Contains(row.Detail.String, DialogStall) {
		return row.Detail.String
	}
	why := fmt.Sprintf("%s's terminal is showing a %s no standing rule answers - nothing was typed into it. %s", agent, DialogStall, seen.Reason())
	check(m.Agents.RaiseDecision(m.Q, agent, DialogDecision+" "+why, why))
	check(db.SetAgentState(m.Q, agent, states.Stalled, 0, clip300(why)))
	m.Log("  %s: %s", agent, why)
	return why
}

// raiseTrustPrompt: the agent's vendor is sitting at its folder-trust screen - nothing is typed, since typed text is not a choice there,
// it is a coin flip landing on whatever the vendor's own default is (claude's: "No, exit"). STALLED and raised to the founder rather
// than retried: the screen does not resolve itself.
func (m *Manager) raiseTrustPrompt(agent, handle, shown, recorded string) (bool, string) {
	// Already raised: a later wake - the next tick, an ask, a key on the bridge - meets the same screen and would raise the same request again.
	if row := must(db.GetAgentState(m.Q, agent)); row != nil && row.State == states.Stalled && strings.Contains(row.Detail.String, TrustStall) {
		return false, row.Detail.String
	}
	acct := m.Agents.CurrentAccount(m.Q, agent)
	if acct == "" {
		acct = "none"
	}
	if shown == "" {
		shown = "an unreadable folder"
	}
	if recorded == "" {
		recorded = "unknown"
	}
	why := fmt.Sprintf("%s's terminal is showing %s's %s for %s (account %s, recorded worktree %s) - nothing was typed into it",
		agent, m.Agents.VendorName(agent), TrustStall, shown, acct, recorded)
	check(m.Agents.RaiseDecision(m.Q, agent, TrustDecision+" "+why, why))
	check(db.SetAgentState(m.Q, agent, states.Stalled, 0, clip300(why)))
	m.Log("  %s: %s", agent, why)
	return false, why
}
