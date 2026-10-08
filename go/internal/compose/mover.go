package compose

import (
	"database/sql"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
	"github.com/handline/handline/go/internal/usage"
)

// newMover is the handoff and the move over what the daemon owns: the usage readings, the note, and the structured sessions.
func (p *Parts) newMover(m *lifecycle.Manager, uenv *usage.Env, env *supervisor.Env, now func() int64) *lifecycle.Mover {
	tu := tickUsage{p: p, u: uenv}
	ho := func() interface {
		Hold(agent string, now int64) (bool, error)
	} {
		return p.LC.Handoff
	}
	return &lifecycle.Mover{M: m, E: lifecycle.MoveEnv{
		Reading: func(q db.Q, agent string) (string, string, bool) {
			r, ok, err := team.ReadAccount(q.(*sql.DB), agent, func() string { return uenv.ReportedAccount(agent) })
			if err != nil || !ok {
				return "", "", false
			}
			return r.Account, r.Source, true
		},
		Windows: func(acct string) lifecycle.Windows {
			st := uenv.Read(acct)
			return lifecycle.Windows{ShortReset: st.Short.Reset, LongReset: st.Long.Reset, ShortS: float64(st.Short.Minutes * 60)}
		},
		ColdStartPct: func(q db.Q) *float64 {
			pct, ok, err := usage.ColdStartPct(q)
			if err != nil || !ok {
				return nil
			}
			return &pct
		},
		AtLimit: func(q db.Q, agent string) (int64, bool) {
			return tu.limitAt(q, agent)
		},
		NoteHold: func(_ db.Q, agent string, at int64) bool {
			hold, _ := ho().Hold(agent, at)
			return hold
		},
		BackgroundWork: func(q db.Q, agent string) string {
			if ok, _ := env.HasBackgroundWork(q, agent); ok {
				return "background work"
			}
			return ""
		},
		ContextUse: func(q db.Q, agent string) (int, int) {
			acct := m.Agents.CurrentAccount(q, agent)
			st := tickUsage{p: p, u: uenv, roster: p.LC.roster}.ReadForAgent(agent, acct)
			if st.TrustedCtxUsed == nil {
				return -1, 0
			}
			return *st.TrustedCtxUsed, tickUsage{p: p, u: uenv, roster: p.LC.roster}.ContextLimit(agent, st.ModelID)
		},
		RunningAccount: p.OpenedOn,
		MeasureForget:  func(q db.Q, agent string) { _ = usage.ForgetColdStart(q, agent) },
		StayWithin:     tick.StayWithin,
		StayOrMove:     tick.StayOrMove,
		WakeAfter:      tick.WakeAfter,
	}}
}
