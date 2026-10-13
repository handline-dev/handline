package compose

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
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

// Wall is the account that holds back a wake that is due: the lifecycle's wait for it, only while something is trying to wake the agent.
func (d delivery) Wall(q db.Q, agent string, row *db.AgentState) (*reads.Wall, error) {
	if d.p.LC == nil {
		return nil, nil
	}
	if yes, err := d.p.LC.Manager.WouldWake(row); err != nil || !yes {
		return nil, err
	}
	w := d.p.LC.Launch.WakeWait(q, agent, row)
	if w == nil {
		return nil, nil
	}
	out := &reads.Wall{Account: w.Account, Assumed: w.Assumed}
	if !w.Slot {
		out.Clock = w.Reset
	}
	return out, nil
}

// ForgottenResolve is the supervisor's: the head request told and still open, with how long the agent has sat idle on it.
func (d delivery) ForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) (int64, int64, bool, error) {
	if d.p.LC == nil {
		return 0, 0, false, nil
	}
	f, err := d.p.LC.Env.ForgottenResolve(q, agent, row, now)
	if err != nil || f == nil {
		return 0, 0, false, err
	}
	return f.Head.ID, f.IdleS, true, nil
}

// WakeWaitReason is the wording ps prints for a wake that is due and walled.
func (d delivery) WakeWaitReason(q db.Q, agent string, row *db.AgentState) (string, error) {
	if d.p.LC == nil {
		return "", nil
	}
	if yes, err := d.p.LC.Manager.WouldWake(row); err != nil || !yes {
		return "", err
	}
	return d.p.LC.Launch.WakeWaitReason(q, agent, row), nil
}

// Trigger is what the policy tick would decide: the same reading, context limit and Evaluate it judges by. For an agent whose triggers do
// not apply, the wording ps prints for a wake that is due and walled.
func (d delivery) Trigger(q db.Q, agent string, row *db.AgentState, account string) (reads.Trigger, error) {
	if d.p.LC == nil {
		return reads.Trigger{}, nil
	}
	if !d.p.LC.Manager.TriggersApplyTo(agent, row) {
		why, err := d.WakeWaitReason(q, agent, row)
		return reads.Trigger{Reason: why}, err
	}
	tu := tickUsage{p: d.p, u: d.p.LC.usage, roster: d.p.LC.roster}
	st := tu.ReadForAgent(agent, account)
	t := tick.Evaluate(st.ShortTrusted, st.TrustedCtxUsed, st.LongTrusted, tu.ContextLimit(agent, st.ModelID))
	return reads.Trigger{Applies: true, Fired: t.Fired, Kind: t.Kind, Reason: t.Reason}, nil
}
