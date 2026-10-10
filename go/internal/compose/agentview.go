package compose

import (
	"github.com/handline/handline/go/internal/agentview"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
)

// agentView builds the writer of agent_view rows. Until the Python daemon stops it writes the rows of structured agents only (Agents is
// structuredAgents and nothing is dropped); at the cutover (TickConfig.Cutover) it also drops the row of an agent that is gone.
func (p *Parts) agentView(cfg Config, cutover bool, tu tickUsage) *agentview.Env {
	lc, q, home := p.LC, p.db, cfg.Home
	return &agentview.Env{
		Q: q, Home: home,
		Sessions:   p.Run,
		Background: lc.Env,
		Wakes:      lc.Manager,
		Prompts:    &prompts.Env{DB: p.db, Home: home, Now: nowOf(cfg.Now)},
		NeedsYou:   needsYou(home, q),
		Agents:     p.structuredAgents,
		Registered: func() []string { return paths.ListAgents(home) },
		Config: func(agent string) (string, string, bool) {
			c, err := lc.roster.Read(agent)
			if err != nil {
				return "", "", false
			}
			_, model, effort := team.Parts(c)
			return model, effort, true
		},
		Account: func(agent string) (string, error) { return team.CurrentAccount(q, agent) },
		Parent: func(agent string) string {
			tm, err := team.LoadTeam(home)
			if err != nil || tm == nil {
				return ""
			}
			return tm.SupervisorOf(agent)
		},
		CacheLeft: func(agent string, now float64) (float64, bool, error) {
			acct, _ := team.CurrentAccount(q, agent)
			c := handoff.ReadClock(tu.ReadForAgent(agent, acct).TranscriptPath)
			if c == nil {
				return 0, false, nil
			}
			left, ok := c.Left(now)
			return left, ok, nil
		},
		CtxPct: func(agent, acct string) *int {
			st := tu.ReadForAgent(agent, acct)
			limit := tu.ContextLimit(agent, st.ModelID)
			if st.TrustedCtxUsed == nil || limit <= 0 {
				return nil
			}
			pct := *st.TrustedCtxUsed * 100 / limit
			return &pct
		},
		Now:      func() int64 { return nowOf(cfg.Now)().Unix() },
		Log:      func(s string) { cfg.Log("%s", s) },
		DropGone: cutover,
	}
}

// agentViewPass is the tick's pass that stores the views.
func agentViewPass(av *agentview.Env) tick.NamedSweep {
	return tick.NamedSweep{Name: "agent-view", Run: func(db.Q, int64) error { return av.Refresh() }}
}
