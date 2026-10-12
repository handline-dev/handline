package lifecycle

import (
	"fmt"
	"slices"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
)

// ForceMove is daemon/handoff.py force_move: the agent's live session moves to dest now, through the handoff's own move, and a closed agent's
// next wake starts it there (move_owed). Nothing is ended unless dest takes one more agent. by is recorded on the event. Whether it did and
// what it says; a refusal is the first false.
func (v *Mover) ForceMove(agent, dest, by string) (ok bool, said string, err error) {
	defer catch(&err)
	m := v.M
	accts := team.Accounts{Home: m.Home}
	if !slices.Contains(paths.ListAccounts(m.Home), dest) || !accts.LoggedIn(dest) {
		return false, fmt.Sprintf("account '%s' does not exist or is not logged in", dest), nil
	}
	here := m.Agents.CurrentAccount(m.Q, agent)
	if here == dest {
		return false, fmt.Sprintf("%s is already on %s", agent, dest), nil
	}
	row := must(db.GetAgentState(m.Q, agent))
	if row == nil || !(isClosed(row) || m.sessionAlive(agent)) {
		return false, agent + " has no session to move", nil
	}
	if row.State == states.Handoff || row.State == states.Stalled {
		return false, fmt.Sprintf("%s is %s", agent, row.State), nil
	}
	if room, why := v.forcedPlan(agent, dest); room == "" {
		return false, fmt.Sprintf("%s - not moving %s there", why, agent), nil
	}
	if isClosed(row) { // nothing to end: the move is owed to the next wake, which starts fresh on dest
		must(db.AddEvent(m.Q, agent, MoveOwedKind, db.KV{K: "to", V: dest}, db.KV{K: "by", V: by}))
		return true, fmt.Sprintf("%s is closed: its next wake starts it on %s (moved by %s)", agent, dest, by), nil
	}
	if _, err := v.PerformHandoff(agent, HandoffTrigger{Kind: "forced", Reason: fmt.Sprintf("forced by %s: %s -> %s", by, here, dest)}, dest); err != nil {
		return false, "", err
	}
	if landed := must(db.GetAgentState(m.Q, agent)); landed == nil || landed.Account.String != dest {
		return false, fmt.Sprintf("%s did not move (mid-turn, or the new session did not start on %s) - it stays on %s", agent, dest, here), nil
	}
	must(db.AddEvent(m.Q, agent, "forced_move", db.KV{K: "from", V: here}, db.KV{K: "to", V: dest}, db.KV{K: "by", V: by}))
	return true, fmt.Sprintf("%s moved %s -> %s (forced by %s)", agent, here, dest, by), nil
}
