package tick

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// LoginStall is the detail of the stall raised for an account that cannot be left; the reconciler's lift looks for it.
const LoginStall = "login expired"

// Login is daemon/login_expired.py without the screen: the CLI's own authentication_failed StopFailure is the witness, so a
// structured session needs no terminal. A zero Login does nothing.
type Login struct {
	// Expired is the mark on the account (accounts.login_expired): why, and whether it is there.
	Expired func(acct string) (why string, ok bool)
	// AuthFailed is the agent's newest authentication_failed turn on acct that no lift has passed: its text, and ok when the agent's
	// last turn ended on one there.
	AuthFailed func(q db.Q, agent, acct string) (why string, ok bool)
	// Mark records the expiry on the account and the event for it; false when the account is already marked (the first sighting stands).
	Mark func(q db.Q, agent, acct, why string) bool
}

// loginExpired is login_expired.check: an agent on an account whose login expired leaves it, level-triggered on the account's mark.
// True when the agent was dealt with and the tick has nothing more to do for it.
func (t *Tick) loginExpired(agent string, row *db.AgentState, acct string, actions *[]string) bool {
	l := t.Login
	if l.Expired == nil || acct == "" || row == nil || closedAt(row) ||
		(row.State != states.Running && row.State != states.Idle && row.State != states.Queued) {
		return false
	}
	why, marked := l.Expired(acct)
	if !marked {
		text, failed := l.AuthFailed(t.Q, agent, acct)
		if !failed {
			return false
		}
		why = fmt.Sprintf("%s's turn ended on authentication_failed%s", agent, text)
		if l.Mark(t.Q, agent, acct, why) {
			t.logf("  %s: login expired (%s) - marked logged out, no launch lands on it", acct, why)
		}
	}
	dest, noMove := t.Handoff.MovePlan(t.Q, agent, "login")
	if dest == "" {
		// Nothing is waiting out a window here, so a retry loop would only fail again: the agent is shown as stopped, and the
		// reconciler's lift releases it once the account logs in.
		detail := fmt.Sprintf("%s on %s and no other account can take it (%s) - hl login %s", LoginStall, acct, noMove, acct)
		check(db.SetAgentState(t.Q, agent, states.Stalled, 0, cut(detail, 300)))
		*actions = append(*actions, fmt.Sprintf("%s: STALLED, %s on %s", agent, LoginStall, acct))
		t.logf("  %s: %s", agent, detail)
		return true
	}
	ok := t.Handoff.PerformHandoff(t.Q, agent, Trigger{true, "login", fmt.Sprintf("%s on %s: %s", LoginStall, acct, why)}, dest)
	suffix := ""
	if !ok {
		suffix = " -> degraded"
	}
	*actions = append(*actions, fmt.Sprintf("%s: handoff (login)%s", agent, suffix))
	return true
}

func cut(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}
