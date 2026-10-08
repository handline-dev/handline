package compose

import (
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
	"github.com/handline/handline/go/internal/usage"
)

// This file builds the policy tick over the structured sessions. The tick supervises the structured agents only: a terminal agent is the
// Python daemon's, and a Go tick over it would find no terminal and count that against it. What the Go side has no implementation of
// yet (performing a handoff, moving an agent to another account, folding the day) answers that it is not served, and the tick then
// leaves the agent as it is.

// Tick is the policy tick and what it needs beside the lifecycle.
type Tick struct {
	Tick *tick.Tick
	Loop *tick.Loop
}

// TickConfig is what only the daemon main knows beyond Config.
type TickConfig struct {
	// Stop ends the loop when closed.
	Stop <-chan struct{}
}

// Tick builds the tick. The lifecycle must be built and the database bound.
func (p *Parts) Tick(cfg Config, tc TickConfig) *Tick {
	lc, q, home := p.LC, p.db, cfg.Home
	env := lc.Env
	uenv := lc.usage
	now := func() int64 { return nowOf(cfg.Now)().Unix() }
	logLine := func(s string) { cfg.Log("%s", s) }
	reconciler := &lifecycle.Reconciler{Q: q, Log: logLine, Ports: lifecycle.ReconcilerPorts{
		Agents:   p.structuredAgents,
		Observe:  func([]string, map[string]string) map[string]session.Observed { return map[string]session.Observed{} },
		NoteSeen: func(map[string]session.Observed, time.Time) {},
		ResumeHandoff: func(db.Q, db.HandoffRow) error {
			return errNotServed("resuming a handoff left in flight")
		},
		EnforceEdges: func(q db.Q, agent string) error { _, err := env.EnforceEdges(q, agent); return err },
		WaitingFor:   func(q db.Q, agent string) (bool, error) { return lc.Manager.WaitingFor(agent) },
		WakeAgent: func(q db.Q, agent string) (bool, string, error) {
			return lc.Manager.WakeAgent(agent, lifecycle.WakeOpts{})
		},
		QueuedOrIdle: func(q db.Q, agent, why string) (string, error) { return queuedOrIdle(q, agent, why) },
		// The stored view is the Python daemon's to keep: agent_view has no Go writer yet (see the note on refreshView).
		RefreshView:      func(db.Q, string) {},
		LiftLogin:        func(db.Q, string, *db.AgentState, *[]string) {},
		StillDrawn:       func(string) bool { return false },
		ResumableSession: func(q db.Q, agent string) string { return p.Manager.SessionID(agent) },
		Resolve: func(id int64, why string) error {
			_, _, err := env.Resolve(q, supervisor.ResolveIn{RID: id, Verdict: why, NoSettle: true})
			return err
		},
		Term: noReconcileTerm{},
		Run:  p.Run,
		Now:  now,
	}}
	t := &tick.Tick{Q: q, Log: logLine, Now: now,
		Host:      tickHost{p: p},
		Usage:     tickUsage{p: p, u: uenv, roster: lc.roster},
		Delivery:  tickDelivery{env: env},
		Messaging: tickMessaging{env: env},
		Lifecycle: tickLifecycle{m: lc.Manager},
		Handoff:   tickHandoff{ho: lc.Handoff, port: handoffPort{env: lc.Handoff, q: q, now: nowOf(cfg.Now)}},
		Reconcile: tickReconciler{r: reconciler},
		Sweeps: tick.Sweeps{
			BlockedSweep:    func(db.Q, int64) {},            // dialogs on a screen: a structured session has none
			CollectSnapshot: func(db.Q) error { return nil }, // a structured session has no screen to read
			PollUsage:       func(db.Q) error { return nil },
			Rollover:        func(string) (bool, string) { return false, "" }, // folding the day is a model call that stays Python's
			Today:           func() string { return nowOf(cfg.Now)().Format("2006-01-02") },
		},
		Observe: tickObserve{u: uenv, home: home},
		Config:  tickConfig{roster: lc.roster, set: lc.Manager.Settings},
	}
	loop := &tick.Loop{Tick: t, Log: logLine, Stop: tc.Stop}
	return &Tick{Tick: t, Loop: loop}
}

func (p *Parts) structuredAgents() []string {
	var out []string
	for _, a := range paths.ListAgents(p.home) {
		if p.Kind(a) {
			out = append(out, a)
		}
	}
	return out
}

type notServedError string

func (e notServedError) Error() string { return string(e) + " is not served by this daemon yet" }

func errNotServed(what string) error { return notServedError(what) }

// queuedOrIdle is daemon/evaluate.queued_or_idle: QUEUED when something has not reached the agent yet, IDLE otherwise.
func queuedOrIdle(q db.Q, agent, when string) (string, error) {
	n, err := db.UnreachedCount(q, agent)
	if err != nil {
		return "", err
	}
	if n > 0 {
		return states.Queued, db.SetAgentState(q, agent, states.Queued, 0, when)
	}
	return states.Idle, db.SetAgentState(q, agent, states.Idle, 0, when+"; no requests waiting")
}

// noReconcileTerm: there is no terminal for the reconciler to look at.
type noReconcileTerm struct{ noTerminal }

func (noReconcileTerm) TrustPromptGone(string) bool { return true }
func (noReconcileTerm) IdleNow(string) bool         { return false }

// tickHost is tick.Host over the structured sessions.
type tickHost struct{ p *Parts }

func (h tickHost) Agents() []string   { return h.p.structuredAgents() }
func (h tickHost) Accounts() []string { return paths.ListAccounts(h.p.home) }

func (h tickHost) SessionAlive(agent string) bool { return h.p.Run.Alive(agent) }

// SessionIdle: the CLI is not mid-turn, and no permission prompt is open (a restart must not cut one short).
func (h tickHost) SessionIdle(agent string) bool {
	return h.p.Run.Alive(agent) && !h.p.Run.Busy(agent) && !(h.p.Manager.Broker != nil && h.p.Manager.Broker.Has(agent))
}

// RunningAccount is not known: the CLI's environment is not read, which never contradicts the recorded account.
func (h tickHost) RunningAccount(string) string { return "" }

// tickUsage is tick.Usage over the usage package.
type tickUsage struct {
	p      *Parts
	u      *usage.Env
	roster *team.Roster
}

func statusOf(st *usage.AccountStatus) tick.Status {
	out := tick.Status{ModelID: st.ModelID, SessionID: st.SessionID, TranscriptPath: st.Transcript,
		ShortTrusted: st.ShortTrusted(), LongTrusted: st.LongTrusted(), IsLiveReading: st.IsLiveReading()}
	if used := st.TrustedCtxUsed(); used != nil {
		n := int(*used)
		out.TrustedCtxUsed = &n
	}
	if st.Short.At != nil {
		out.ShortAt = *st.Short.At
	}
	out.ShortPct = st.Short.Pct
	return out
}

func (t tickUsage) AccountReading(q db.Q, agent string) (tick.AccountReading, bool) {
	r, ok, err := team.ReadAccount(t.p.db, agent, func() string { return t.u.ReportedAccount(agent) })
	if err != nil || !ok {
		return tick.AccountReading{}, false
	}
	return tick.AccountReading{Account: r.Account, Source: r.Source}, true
}

func (t tickUsage) ReadForAgent(agent, acct string) tick.Status {
	return statusOf(t.u.ReadForAgent(agent, acct))
}

// ContextLimit is status.effective_context_limit: the agent's own figure, held to a share of the model's window when the window is known.
func (t tickUsage) ContextLimit(agent, modelID string) int {
	cfg, _ := t.roster.Read(agent)
	limit := int(team.ContextTokensOf(cfg))
	if size := contextWindow(modelID); size > 0 {
		limit = min(limit, int(float64(size)*tick.ContextClampFraction))
	}
	return limit
}

// ctxSizes is vendors/claude_collect.CTX_SIZE, keyed by prefix so a dated model id still matches. An unrecognised model has no figure
// rather than a guessed one.
var ctxSizes = []struct {
	prefix string
	size   int
}{{"claude-opus-5", 1_000_000}, {"claude-sonnet-5", 1_000_000}, {"claude-haiku-4-5", 200_000}}

func contextWindow(model string) int {
	for _, c := range ctxSizes {
		if len(model) >= len(c.prefix) && model[:len(c.prefix)] == c.prefix {
			return c.size
		}
	}
	return 0
}

func (t tickUsage) WriteBurn(acct string, rate float64, at int64) {
	usage.WriteBurn(t.p.home, acct, rate, at)
}

func (t tickUsage) AtLimit(q db.Q, agent string) *tick.Limit {
	at, err := handoff.AtLimit(q, agent)
	if err != nil || at == nil {
		return nil
	}
	text, _ := at["text"].(string)
	source, _ := at["source"].(string)
	return &tick.Limit{Text: text, Source: source}
}

func (t tickUsage) Settle(q db.Q, agent string) { t.u.SettleColdStart(q, agent) }

// tickDelivery is tick.Delivery over the supervisor's delivery; a call that fails says nothing, as Python's does.
type tickDelivery struct{ env *supervisor.Env }

func (d tickDelivery) EnforceEdges(q db.Q, agent string) error {
	_, err := d.env.EnforceEdges(q, agent)
	return err
}
func (d tickDelivery) NoteFailure(agent, why string) { d.env.NoteFailure(agent, why) }
func (d tickDelivery) TrackBackgroundWork(q db.Q, agent string, row *db.AgentState) {
	d.env.TrackBackgroundWork(q, agent, row)
}
func (d tickDelivery) DeliverAnswers(q db.Q, agent string) { d.env.DeliverAnswers(q, agent) }
func (d tickDelivery) DeliverQueued(q db.Q, agent string) bool {
	sent, _ := d.env.DeliverQueued(q, agent)
	return sent
}
func (d tickDelivery) NudgeForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) {
	d.env.NudgeForgottenResolve(q, agent, row, now)
}
func (d tickDelivery) SessionPresent(agent string) bool {
	ok, _ := d.env.SessionPresent(agent)
	return ok
}
func (d tickDelivery) HasBackgroundWork(q db.Q, agent string) bool {
	ok, _ := d.env.HasBackgroundWork(q, agent)
	return ok
}
func (d tickDelivery) IdleSince(q db.Q, agent string) (int64, bool) {
	at, ok, _ := d.env.IdleSince(q, agent)
	return at, ok
}

// tickMessaging is tick.Messaging over the supervisor's landing and messaging.
type tickMessaging struct{ env *supervisor.Env }

func (m tickMessaging) LandingSweep(q db.Q, agent string)    { m.env.LandingSweep(q, agent) }
func (m tickMessaging) TellUndelivered(q db.Q, agent string) { m.env.TellUndelivered(q, agent) }
func (m tickMessaging) DeliverPending(q db.Q, agent string)  { m.env.DeliverPending(q, agent) }

// tickLifecycle is tick.Lifecycle over the lifecycle manager.
type tickLifecycle struct{ m *lifecycle.Manager }

func sessionOf(st tick.Status) lifecycle.SessionStatus {
	return lifecycle.SessionStatus{SessionID: st.SessionID}
}

func (l tickLifecycle) WaitingFor(_ db.Q, agent string) bool {
	ok, _ := l.m.WaitingFor(agent)
	return ok
}
func (l tickLifecycle) ResumableWithoutTerminal(row *db.AgentState) bool {
	return l.m.ResumableWithoutTerminal(row)
}
func (l tickLifecycle) WakeAgent(_ db.Q, agent string) (bool, string) {
	ok, said, err := l.m.WakeAgent(agent, lifecycle.WakeOpts{})
	if err != nil {
		return false, err.Error()
	}
	return ok, said
}
func (l tickLifecycle) StartOrWakeNeeded(_ db.Q, agent string, row *db.AgentState) *tick.Need {
	kind, what, err := l.m.StartOrWakeNeeded(agent, row)
	if err != nil || kind == "" {
		return nil
	}
	return &tick.Need{Kind: kind, What: what}
}
func (l tickLifecycle) StartForWork(_ db.Q, agent string) string {
	said, err := l.m.StartForWork(agent)
	if err != nil {
		return agent + ": could not start for work: " + err.Error()
	}
	return said
}
func (l tickLifecycle) ColdBeforeSend(_ db.Q, agent string, row *db.AgentState, now int64, st tick.Status) *bool {
	tried, closed, err := l.m.ColdBeforeTyping(agent, row, now, sessionOf(st))
	if err != nil || !tried {
		return nil
	}
	return &closed
}
func (l tickLifecycle) ShouldClose(_ db.Q, agent, state string, row *db.AgentState, now int64, st tick.Status) bool {
	yes, _ := l.m.ShouldClose(agent, state, row, now, st.SessionID)
	return yes
}
func (l tickLifecycle) CloseAgent(_ db.Q, agent string, st tick.Status) (bool, string) {
	ok, said, err := l.m.CloseAgent(agent, sessionOf(st), lifecycle.CloseOpts{})
	if err != nil {
		return false, err.Error()
	}
	return ok, said
}
func (l tickLifecycle) CloseFresh(_ db.Q, agent string, st tick.Status) (bool, string) {
	ok, said, err := l.m.CloseFresh(agent, sessionOf(st), lifecycle.CloseOpts{})
	if err != nil {
		return false, err.Error()
	}
	return ok, said
}
func (l tickLifecycle) RestartAgent(_ db.Q, agent string, st tick.Status, by string, applying []string, cause string) (bool, string) {
	ok, said, err := l.m.RestartAgent(agent, sessionOf(st), lifecycle.RestartOpts{By: by, Applying: applying, Cause: cause})
	if err != nil {
		return false, err.Error()
	}
	return ok, said
}
func (l tickLifecycle) TriggersApply(row *db.AgentState) bool {
	return row != nil && l.m.TriggersApplyTo(row.Agent, row)
}

// tickHandoff is tick.Handoff: the note is real; performing a handoff and moving an agent to another account are not served yet, and the
// tick then leaves the agent where it is.
type tickHandoff struct {
	ho   *handoff.Env
	port handoffPort
}

func (tickHandoff) SettleOwedClear(db.Q, string) bool { return false } // a structured session has no context to clear by key
func (h tickHandoff) Movable(kind string) bool        { return sessions{}.MovableTrigger(kind) }
func (h tickHandoff) MovePlan(q db.Q, agent, kind string) (string, string) {
	return h.port.MovePlan(q, agent, kind)
}
func (tickHandoff) MoveToRoom(db.Q, string, tick.Trigger) bool             { return false }
func (tickHandoff) PerformHandoff(db.Q, string, tick.Trigger, string) bool { return false }
func (h tickHandoff) NoteHold(_ db.Q, agent string, now int64) bool {
	hold, _ := h.ho.Hold(agent, now)
	return hold
}
func (h tickHandoff) NoteAtCacheEdge(_ db.Q, agent string, now int64, st tick.Status) {
	h.ho.AtCacheEdge(agent, now, st.TranscriptPath)
}
func (h tickHandoff) NoteEnabled(agent string) bool { return h.ho.Enabled(agent) }
func (h tickHandoff) NoteAsked(_ db.Q, agent string) (int, bool) {
	_, prompts, ok, err := h.ho.Asked(agent)
	return prompts, err == nil && ok
}
func (h tickHandoff) NoteAsk(_ db.Q, agent, why string) bool {
	ok, _ := h.ho.Ask(agent, why)
	return ok
}
func (h tickHandoff) NoteAnsweredTurn(_ db.Q, agent string) bool {
	ok, _ := h.ho.AnsweredTurn(agent)
	return ok
}
func (h tickHandoff) NoteText(_ db.Q, agent string) (string, bool) {
	text, err := h.ho.NoteText(agent)
	return text, err == nil && text != ""
}
func (tickHandoff) AskMarginS() int { return handoff.AskMarginS }

// tickReconciler is tick.Reconciler over the lifecycle's reconciler.
type tickReconciler struct{ r *lifecycle.Reconciler }

func (t tickReconciler) Sweep(_ db.Q, actions *[]string) { t.r.Sweep(actions) }
func (t tickReconciler) ExpireHandoffs(db.Q) []string {
	out, _ := t.r.ExpireHandoffs()
	return out
}
func (t tickReconciler) StallAgent(_ db.Q, agent string, actions *[]string, err error, kind string) {
	t.r.StallAgent(agent, actions, err, kind)
}

// tickObserve is tick.Observe over the usage package.
type tickObserve struct {
	u    *usage.Env
	home string
}

func (o tickObserve) ActiveByAccount(q db.Q) map[string][]string {
	by, _ := o.u.ActiveByAccount(q)
	return by
}
func (o tickObserve) FirstTurnAgents(q db.Q) map[string]bool {
	firsts, _ := usage.FirstTurnAgents(q)
	return firsts
}
func (o tickObserve) ReadAccount(acct string) tick.Status { return statusOf(o.u.Read(acct)) }
func (o tickObserve) ObserveBurn(q db.Q, acct string, working []string, _ tick.Status, firsts map[string]bool) error {
	return o.u.Observe(q, acct, working, o.u.Read(acct), firsts)
}
func (o tickObserve) RecordUsageSample(q db.Q, acct string, _ tick.Status) error {
	return o.u.RecordSample(q, acct, o.u.Read(acct), int64(time.Now().Unix()))
}
func (o tickObserve) PruneUsageSamples(q db.Q) error { _, err := o.u.PruneSamples(q); return err }
func (o tickObserve) IngestTokens(q db.Q, acct string) error {
	_, err := o.u.IngestTokens(q, acct, usage.BytesPerTick)
	return err
}
func (o tickObserve) PruneTokens(q db.Q) error { _, err := o.u.PruneTokens(q); return err }
func (o tickObserve) RefreshLongRatios(q db.Q) error {
	return o.u.RefreshLongRatios(q, time.Now().Unix(), false)
}

// tickConfig is tick.Config over agent.yaml and config.yaml.
type tickConfig struct {
	roster *team.Roster
	set    *lifecycle.Settings
}

func (c tickConfig) AgentConfig(agent string) map[string]any {
	cfg, err := c.roster.Read(agent)
	if err != nil {
		return map[string]any{}
	}
	return cfg
}
func (c tickConfig) Seconds(key string, def int64) int64 { return c.set.ConfigSeconds(key, def) }
