package compose

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

// delivery is reads.Delivery over the lifecycle the parts hold: the three things a queued chat line's reason needs that the database does
// not. It holds no state of its own. Before Lifecycle has built the lifecycle it knows nothing and says so (no wall, no wake cause, no full
// account).
type delivery struct{ p *Parts }

var _ reads.Delivery = delivery{}

// Delivery is the reads' view of why a queued line waits (daemon.Config.Delivery).
func (p *Parts) Delivery() reads.Delivery { return delivery{p} }

// WallReason is why typing at the agent would only spend quota: the supervisor's own answer.
func (d delivery) WallReason(q db.Q, agent string, row *db.AgentState) (string, error) {
	if d.p.LC == nil {
		return "", nil
	}
	return d.p.LC.Env.WallReason(q, agent, row)
}

// ClosedWakeCause is what the next tick wakes a closed agent for: the lifecycle's one list.
func (d delivery) ClosedWakeCause(agent string, row *db.AgentState) (string, error) {
	if d.p.LC == nil {
		return "", nil
	}
	c, err := d.p.LC.Manager.ClosedWakeCause(agent, row)
	if err != nil || c == nil {
		return "", err
	}
	return c.What, nil
}

// AccountFull is the agent's account and its short-window percent, when that is at the block.
func (d delivery) AccountFull(agent string) (string, int, bool) {
	if d.p.LC == nil || d.p.db == nil {
		return "", 0, false
	}
	acct, err := team.CurrentAccount(d.p.db, agent)
	if err != nil || acct == "" {
		return "", 0, false
	}
	pct := d.p.LC.usage.Read(acct).ShortTrusted()
	if pct == nil || *pct < usage.ShortBlock {
		return acct, 0, false
	}
	return acct, *pct, true
}
