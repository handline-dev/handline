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

// loginLifted is the cursor holding the id of the failed turn a lift has passed. That turn stays the agent's last event
// until it is prompted again, and without this it would stall the account a second time.
// It is the Go daemon's own: Python's "login_lifted:<agent>" holds a session row id, and one name for both would be read as the other's.
func loginLifted(agent string) string { return "login_lifted_event:" + agent }

// pythonLifted is when Python's lift last ran for the agent, 0 if never. A refusal at or before it was lifted there: the cursor of
// that daemon is a session row id this one cannot compare with an event id, so its time is.
func pythonLifted(q db.Q, agent string) int64 {
	at, _, _ := db.CursorUpdated(q, "login_lifted:"+agent)
	return at
}

// lifted is whether a lift has passed a failure on acct: the agent's own lift cursor, Python's lift, or a login since.
func lifted(q db.Q, accounts team.Accounts, agent, acct string, f handoff.Failure) bool {
	cursor, _ := db.Cursor(q, loginLifted(agent))
	return f.ID <= cursor || f.TS <= pythonLifted(q, agent) || accounts.LoggedInSince(acct, f.TS)
}

// stalled is the account's stall, unless a Stop on the account since it was set says the account works again: that ends it.
func stalled(q db.Q, accounts team.Accounts, acct string) *team.Stall {
	e := accounts.Stalled(acct)
	if e == nil {
		return nil
	}
	var stop int64
	if q.QueryRow("SELECT COALESCE(MAX(ts), 0) FROM events WHERE account = ? AND kind = 'Stop'", acct).Scan(&stop) == nil && float64(stop) >= e.At {
		accounts.ClearStall(acct)
		return nil
	}
	return e
}

// noRoomTold is the meta holding the stall or end the founder was last told acct's agents have no other account for.
func noRoomTold(acct string) string { return "noroom_told:" + acct }

// raiser opens a decision for the founder (founderAsker).
type raiser interface {
	RaiseDecision(q db.Q, agent, text, reason string) (int64, error)
}

// loginPorts is tick.Login over the account stalls, the month cycle's end and the events table.
func loginPorts(accounts team.Accounts, ended func(acct string) string, founder raiser) tick.Login {
	return tick.Login{
		Ended: ended,
		NoRoom: func(q db.Q, agent, acct, key, text string) {
			if told, _, _ := db.Meta(q, noRoomTold(acct)); told == key {
				return
			}
			if _, err := founder.RaiseDecision(q, agent, "No account can take "+acct+"'s agents\n\n"+text, "no account has room"); err == nil {
				_ = db.SetMeta(q, noRoomTold(acct), key)
			}
		},
		Stalled: func(q db.Q, acct string) (string, string, bool) {
			e := stalled(q, accounts, acct)
			if e == nil {
				return "", "", false
			}
			return e.Why, e.Error, true
		},
		Failed: func(q db.Q, agent, acct string) (string, string, int64, bool) {
			f, ok, err := handoff.LastFailure(q, agent, acct)
			if err != nil || !ok || lifted(q, accounts, agent, acct, f) {
				return "", "", 0, false
			}
			return f.Error, f.Details, f.TS, true
		},
		Repeats: func(q db.Q, acct, errType string) int {
			times, _ := handoff.Failures(q, acct, errType)
			n := 0
			for _, ts := range times {
				if !accounts.LoggedInSince(acct, ts) {
					n++
				}
			}
			return n
		},
		Stall: func(q db.Q, agent, acct, errType, why string, at int64) bool {
			// Stamped at the end of the second the failure is dated in, which is as late as it could have been.
			if first, err := accounts.StallAccount(acct, errType, why, time.Unix(at+1, 0)); err != nil || !first {
				return false
			}
			_, _ = db.AddEventFor(q, agent, acct, "login_expired", db.KV{K: "account", V: acct}, db.KV{K: "error", V: errType}, db.KV{K: "why", V: why})
			return true
		},
	}
}

// liftLogin is login_expired.lift: an agent stalled with its account is released once the account's stall ends.
func liftLogin(accounts team.Accounts) func(db.Q, string, *db.AgentState, *[]string) {
	return func(q db.Q, agent string, row *db.AgentState, actions *[]string) {
		if row.State != states.Stalled || !strings.Contains(row.Detail.String, tick.LoginStall) {
			return
		}
		acct, _ := team.CurrentAccount(q, agent)
		if acct != "" && stalled(q, accounts, acct) != nil {
			return
		}
		if f, ok, _ := handoff.LastFailure(q, agent, acct); ok {
			_ = db.SetCursor(q, loginLifted(agent), f.ID)
		}
		if acct == "" {
			acct = "its account"
		}
		_ = db.SetAgentState(q, agent, states.Idle, 0, acct+" is usable again; the stall is lifted")
		*actions = append(*actions, fmt.Sprintf("%s: STALLED -> IDLE (%s usable again)", agent, acct))
	}
}
