package lifecycle

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/usage"
)

// UsageAdapter is the UsagePort the launch and the wake read, over the usage package's status, pick and load.
type UsageAdapter struct {
	Env *usage.Env
	Q   db.Q
}

var _ UsagePort = (*UsageAdapter)(nil)

func (u *UsageAdapter) ShortBlock() int { return usage.ShortBlock }
func (u *UsageAdapter) LongBlock() int  { return usage.LongBlock }

// Read is status.read of the account.
func (u *UsageAdapter) Read(acct string) AccountStatus {
	st := u.Env.Read(acct)
	out := AccountStatus{Exists: st.Exists, Stale: st.Stale(), AgeS: st.AgeS(), ShortTrusted: st.ShortTrusted(), LongTrusted: st.LongTrusted(),
		ShortLabel: st.Short.Label(), LongLabel: st.Long.Label(), ShortReset: st.Short.Reset, LongReset: st.Long.Reset}
	return out
}

// PickAccount is status.pick_account as find_room asks it: a never-run account is a real place to go, and this is the one caller about
// to place an agent, so a carried or idle-floored candidate is asked fresh. A load or a pick that cannot be read picks nobody: fail closed.
func (u *UsageAdapter) PickAccount(candidates []string, agent string) string {
	loads, err := u.Env.ReadAll(u.Q, agent)
	if err != nil {
		return ""
	}
	picked, _, err := u.Env.Pick(u.Q, usage.PickOpts{Candidates: candidates, Bootstrap: true, Load: loads, Agent: agent, LiveRefresh: true})
	if err != nil {
		return ""
	}
	return picked
}

// NotFor is whether no agent of vendor is ever placed on the account (another vendor's, unreadable, or its cycle ended).
func (u *UsageAdapter) NotFor(acct, vendor string) bool { return u.Env.NotFor(acct, vendor) != "" }

// Fit is whether nothing says the account is a bad place to start. A load that cannot be read says nothing is fit.
func (u *UsageAdapter) Fit(acct, agent string) bool {
	loads, err := u.Env.ReadAll(u.Q, agent)
	if err != nil {
		return false
	}
	return usage.Unfit(u.Env.Read(acct), loads[acct], agent) == ""
}

// CapOf is how many agents the account may have mid-turn at once, shrunk by its weekly pace.
func (u *UsageAdapter) CapOf(acct string) int { return u.Env.CapNow(acct) }

// Headroom is what the account's weekly bucket still holds.
func (u *UsageAdapter) Headroom(acct string) (float64, bool) { return u.Env.Headroom(acct) }

// ActiveOn is how many agents are mid-turn on the account, agent left out. A count that cannot be read is the cap: no room.
func (u *UsageAdapter) ActiveOn(q db.Q, acct, agent string) int {
	by, err := u.Env.ActiveAgents(q, agent)
	if err != nil {
		return u.CapOf(acct)
	}
	return by[acct]
}

// StatusAdapter is the StatusPort: what the daemon knows of an agent's session. A terminal agent's session id and context come from
// its status file; a structured agent has none, and its session id is the one stored with it.
type StatusAdapter struct {
	Env        *usage.Env
	Q          db.Q
	Structured func(agent string) bool
}

var _ StatusPort = (*StatusAdapter)(nil)

// ReadForAgent is status.read_for_agent, with the stored session for a structured agent.
func (s *StatusAdapter) ReadForAgent(agent, account string) SessionStatus {
	st := s.Env.ReadForAgent(agent, account)
	out := SessionStatus{SessionID: st.SessionID}
	if st.CtxPct != nil {
		out.CtxPct = *st.CtxPct
	}
	if s.Structured != nil && s.Structured(agent) {
		out = SessionStatus{}
		if row, err := db.GetAgentState(s.Q, agent); err == nil && row != nil {
			out.SessionID = row.ResumeSession.String
		}
	}
	return out
}
