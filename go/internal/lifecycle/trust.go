package lifecycle

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/term"
)

// startupScreens is the relaunch's gate before the wake prompt: a no-op when nothing is showing, else a decision. It runs ahead of
// typeable_now, which refuses these screens as dialogs and would leave them unanswered. The session has just started, so any dialog is a
// start-up one. The deliver paths only check typeable_now, so they wait and the blocked sweep reports it.
func (m *Manager) startupScreens(agent, handle string) (bool, string) {
	defer m.Term.DriverScope()()
	if m.Term.TrustPromptShowing(handle) {
		// The launch marked the worktree trusted in the account's config (Spawns.Trust), so this screen means that write did not happen.
		shown := m.Term.TrustPromptFolder(handle)
		m.Log("  %s: the folder-trust dialog showed for %s although the launch marks its worktree trusted", agent, shown)
		recorded := ""
		if row := must(db.GetAgentState(m.Q, agent)); row != nil {
			recorded = m.Spawn.WorktreePath(row.Worktree.String)
		}
		return m.raiseTrustPrompt(agent, handle, shown, recorded)
	}
	handled, ok, why := m.handleStartupDialog(agent, handle)
	if !handled {
		return true, "clear"
	}
	return ok, why
}

// handleStartupDialog is any other dialog a launch stops at - the vendor's screens change, and each one that was not named left the
// wake failing until it read FATAL with no word of what was on screen. handled is false when there is none. Nothing is pressed: a dialog
// the table (go/internal/dialogs) does not give the daemon to answer is a person's, shown in its own words.
func (m *Manager) handleStartupDialog(agent, handle string) (handled, ok bool, why string) {
	seen := m.Term.ScreenBlocker(handle)
	if seen == nil || seen.Kind != term.Modal {
		return false, false, ""
	}
	return true, false, m.raiseDialogStall(agent, seen)
}

// raiseDialogStall: STALLED, with one decision for the founder, until the dialog is gone: the same shape as the trust stall, which the
// sweep lifts when the screen is read clear.
func (m *Manager) raiseDialogStall(agent string, seen *term.Blocker) string {
	if row := must(db.GetAgentState(m.Q, agent)); row != nil && row.State == states.Stalled && strings.Contains(row.Detail.String, DialogStall) {
		return row.Detail.String
	}
	why := fmt.Sprintf("%s's terminal is showing a %s only a person answers - nothing was typed into it. %s", agent, DialogStall, seen.Reason())
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
