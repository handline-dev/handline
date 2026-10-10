package compose

import (
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
)

// loginLifted is the cursor holding the id of the authentication_failed turn a lift has passed. That turn stays the agent's last event
// until it is prompted again, and without this it would mark the account a second time.
func loginLifted(agent string) string { return "login_lifted:" + agent }

// loginPorts is tick.Login over the account marks and the events table.
func loginPorts(accounts team.Accounts, now func() time.Time) tick.Login {
	return tick.Login{
		Expired: func(acct string) (string, bool) {
			e := accounts.LoginExpired(acct)
			if e == nil {
				return "", false
			}
			return e.Why, true
		},
		AuthFailed: func(q db.Q, agent, acct string) (string, bool) {
			id, details, ok, err := handoff.AuthFailed(q, agent, acct)
			if err != nil || !ok {
				return "", false
			}
			if lifted, _ := db.Cursor(q, loginLifted(agent)); id <= lifted {
				return "", false
			}
			if details != "" {
				details = ": " + details
			}
			return details, true
		},
		Mark: func(q db.Q, agent, acct, why string) bool {
			if first, err := accounts.MarkLoginExpired(acct, why, now()); err != nil || !first {
				return false
			}
			_, _ = db.AddEventFor(q, agent, acct, "login_expired", db.KV{K: "account", V: acct}, db.KV{K: "why", V: why})
			return true
		},
	}
}

// liftLogin is login_expired.lift: a stall raised for an expired login ends once the agent's account is logged in again.
func liftLogin(accounts team.Accounts) func(db.Q, string, *db.AgentState, *[]string) {
	return func(q db.Q, agent string, row *db.AgentState, actions *[]string) {
		if row.State != states.Stalled || !strings.Contains(row.Detail.String, tick.LoginStall) {
			return
		}
		acct, _ := team.CurrentAccount(q, agent)
		if acct != "" && accounts.LoginExpired(acct) != nil {
			return
		}
		if id, _, ok, _ := handoff.AuthFailed(q, agent, acct); ok {
			_ = db.SetCursor(q, loginLifted(agent), id)
		}
		if acct == "" {
			acct = "its account"
		}
		_ = db.SetAgentState(q, agent, states.Idle, 0, acct+" is logged in again; the stall is lifted")
		*actions = append(*actions, fmt.Sprintf("%s: STALLED -> IDLE (%s logged in again)", agent, acct))
	}
}
