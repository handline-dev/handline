package tick

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
)

// LoginStall is in the detail of the stall raised for an account that cannot be left; the reconciler's lift looks for it.
const LoginStall = "no other account can take it"

// RepeatN is how many StopFailures of one error the rules below do not name, on one account with no Stop between, stall it. The worst
// run of a service-side error on one account seen live is 3 (server_error), and one wake can fail twice in a second.
const RepeatN = 4

// unusable are the StopFailure errors that say the account itself cannot be used: one stalls it.
var unusable = map[string]bool{"authentication_failed": true, "oauth_org_not_allowed": true, "account_on_hold": true,
	"verification_required": true, "billing_error": true, "cloud_credential_error": true}

// passing are the errors that never stall an account: rate_limit has the limit path, server_error and overloaded hit every account alike, and
// invalid_request and max_output_tokens are the turn's own. Any other error stalls the account once it repeats RepeatN times.
var passing = map[string]bool{"rate_limit": true, "server_error": true, "overloaded": true, "invalid_request": true, "max_output_tokens": true}

// Login is daemon/login_expired.py without the screen, for every error that makes an account unusable: the CLI's own StopFailure
// is the witness, so a structured session needs no terminal. A zero Login does nothing.
type Login struct {
	// Stalled is the account's stall (accounts.login_expired): why, the error that stalled it, and whether it is there.
	Stalled func(q db.Q, acct string) (why, errType string, ok bool)
	// Failed is the StopFailure the agent's last turn ended on at acct that no lift has passed: its error, its details, and its time.
	Failed func(q db.Q, agent, acct string) (errType, details string, at int64, ok bool)
	// Repeats is how many StopFailures on errType acct has had that no lift has passed.
	Repeats func(q db.Q, acct, errType string) int
	// Stall stalls the account and records the event for it, stamped at (the second of the failure, so a login made since ends it);
	// false when the account is already stalled (the first sighting stands).
	Stall func(q db.Q, agent, acct, errType, why string, at int64) bool
}

// loginExpired is login_expired.check: an agent with a live session on a stalled account leaves it, level-triggered on the
// account's stall. True when the agent was dealt with and the tick has nothing more to do for it.
func (t *Tick) loginExpired(agent string, row *db.AgentState, acct string, actions *[]string) bool {
	l := t.Login
	if l.Stalled == nil || acct == "" || row == nil || closedAt(row) || !t.Host.SessionAlive(agent) ||
		(row.State != states.Running && row.State != states.Idle && row.State != states.Queued) {
		return false
	}
	why, errType, stalled := l.Stalled(t.Q, acct)
	if !stalled {
		var details string
		var at int64
		var failed bool
		if errType, details, at, failed = l.Failed(t.Q, agent, acct); !failed || passing[errType] {
			return false
		}
		if details != "" {
			details = ": " + details
		}
		why = fmt.Sprintf("%s's turn ended on %s%s", agent, errType, details)
		if !unusable[errType] {
			n := l.Repeats(t.Q, acct, errType)
			if n < RepeatN {
				return false
			}
			why += fmt.Sprintf(" (%d times on %s with no Stop between)", n, acct)
		}
		if l.Stall(t.Q, agent, acct, errType, why, at) {
			t.logf("  %s: STALLED (%s), no launch lands on it", acct, why)
		}
	}
	word := team.StallWord(errType)
	dest, noMove := t.Handoff.MovePlan(t.Q, agent, "login")
	if dest == "" {
		// Nothing is waiting out a window here, so a retry loop would only fail again: the agent is shown as stopped, and the
		// reconciler's lift releases it once the account's stall ends.
		detail := fmt.Sprintf("%s on %s and %s (%s) - hl login %s or hl account wake %s", word, acct, LoginStall, noMove, acct, acct)
		check(db.SetAgentState(t.Q, agent, states.Stalled, 0, cut(detail, 300)))
		*actions = append(*actions, fmt.Sprintf("%s: STALLED, %s on %s", agent, word, acct))
		t.logf("  %s: %s", agent, detail)
		return true
	}
	ok := t.Handoff.PerformHandoff(t.Q, agent, Trigger{true, "login", fmt.Sprintf("%s on %s: %s", word, acct, why)}, dest)
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
