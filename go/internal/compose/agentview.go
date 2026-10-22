package compose

import (
	"github.com/handline/handline/go/internal/agentview"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/tick"
	"github.com/handline/handline/go/internal/turns"
)

// rowSessions is what the view knows of the sessions whose rows this daemon writes: with terminals, the terminal agents, whose session is
// the one their session file shows (a status busy is a turn running). Where the hooks have said (turns), they answer busy first: the
// session file still says busy when the Stop hook arrives, so a view written then would stay running.
type rowSessions struct {
	p         *Parts
	terminals bool
	turns     *turns.Tracker
}

func (r rowSessions) Structured(string) bool { return r.terminals }

func (r rowSessions) Alive(agent string) bool {
	if !r.terminals {
		return false
	}
	_, _, found := r.p.terminalSession(agent, r.p.liveTerminals())
	return found != sessionNone // a session file that cannot be read under a live pid is a session that runs
}

func (r rowSessions) Busy(agent string) bool {
	if !r.terminals {
		return false
	}
	if r.turns != nil {
		switch r.turns.State(agent) {
		case turns.Working:
			return true
		case turns.Idle:
			return false
		}
	}
	rec, _, found := r.p.terminalSession(agent, r.p.liveTerminals())
	return found == sessionFound && rec.Status == "busy"
}

// agentView builds the writer of agent_view rows: those of the terminal agents when terminals is on; at the cutover
// (TickConfig.Cutover) it also drops the row of an agent that is gone.
func (p *Parts) agentView(cfg Config, cutover, terminals bool, tu tickUsage) *agentview.Env {
	lc, q, home := p.LC, p.db, cfg.Home
	return &agentview.Env{
		PaneRuns: p.paneRuns,
		Q:        q, Home: home,
		Sessions:   rowSessions{p, terminals, cfg.Turns},
		Background: lc.Env,
		Wakes:      lc.Manager,
		Prompts:    &prompts.Env{DB: p.db, Home: home, Now: nowOf(cfg.Now)},
		NeedsYou:   p.needsYou(),
		Agents:     func() []string { return p.tickAgents(terminals) },
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
		LoadHeld: lc.Gate.Held,
		Walled: func(agent string, row *db.AgentState) *agentview.Wall {
			if yes, err := lc.Manager.WouldWake(row); err != nil || !yes {
				return nil
			}
			w := lc.Launch.WakeWait(q, agent, row)
			if w == nil {
				return nil
			}
			out := &agentview.Wall{Text: lifecycle.DescribeWakeWait(w), Slot: w.Slot, Pace: w.Pace != nil}
			switch {
			case w.Pace != nil:
				out.Reset = w.Pace.BackAt
			case !w.Slot:
				out.Reset = w.Reset
			}
			return out
		},
	}
}

// agentViewPass is the tick's pass that stores the views.
func agentViewPass(av *agentview.Env) tick.NamedSweep {
	return tick.NamedSweep{Name: "agent-view", Run: func(db.Q, int64) error { return av.Refresh() }}
}

// paneRuns is whether the pane agent's row names is up with something other than its shell in front (agentview.Env.PaneRuns).
func (p *Parts) paneRuns(agent string) bool {
	row, err := db.GetAgentState(p.db, agent)
	if err != nil || row == nil || row.Terminal.String == "" || p.LC == nil || p.LC.term == nil {
		return false
	}
	lister, ok := p.LC.term.Backend.(term.PaneLister)
	if !ok {
		return false
	}
	panes, ok := lister.ListPanes()
	if !ok {
		return false
	}
	for _, pane := range panes {
		if pane.Handle == row.Terminal.String {
			return !pane.Dead && !pane.AtShell()
		}
	}
	return false
}
