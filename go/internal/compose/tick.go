package compose

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/dangling"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/fold"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/housekeeping"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/tick"
	"github.com/handline/handline/go/internal/usage"
)

// This file builds the policy tick. It supervises the structured agents; with TickConfig.Terminals it supervises the terminal agents too,
// reading their sessions from <config dir>/sessions/<pid>.json, and a Python daemon must not tick the same agents then. Folding the day is a
// model call that stays the Python daemon's.

// Tick is the policy tick and what it needs beside the lifecycle.
type Tick struct {
	Tick *tick.Tick
	Loop *tick.Loop

	dev       *tick.DevMode
	restarted bool
	// Startup is loop.py's start, run once before the first tick: reconcile, bring up the agents with work waiting, settle old messages,
	// and try every pending one.
	Startup func()
}

// Restarting says the loop ended because the executable changed and started clean: the caller releases the lock and the database, then
// calls Restart.
func (t *Tick) Restarting() bool { return t.restarted }

// Restart execs the daemon's own executable again; it returns only when that fails.
func (t *Tick) Restart() error { return t.dev.Restart() }

// TickConfig is what only the daemon main knows beyond Config.
type TickConfig struct {
	// Stop ends the loop when closed.
	Stop <-chan struct{}
	// Cutover says the Python daemon no longer ticks: this tick then owns the usage poll (account readings, samples, burn, token log) and every agent_view
	// row, gone agents' included. Off, the Python daemon keeps both for its terminal agents and this tick writes the rows of structured
	// agents alone, so two ticks on one home do not double the samples or rewrite each other's rows.
	Cutover bool
	// Terminals puts the terminal agents under this tick: it wakes, closes, restarts and hands off their sessions. Their agent_view rows
	// stay the Python daemon's until the view covers them.
	Terminals bool
	// ScratchRoot is the sessions' scratch root whose stale repo copies are deleted; empty sweeps none (a test must not delete from the
	// machine's real one).
	ScratchRoot string
}

// Tick builds the tick. The lifecycle must be built and the database bound.
func (p *Parts) Tick(cfg Config, tc TickConfig) *Tick {
	lc, q, home := p.LC, p.db, cfg.Home
	env := lc.Env
	uenv := lc.usage
	now := func() int64 { return nowOf(cfg.Now)().Unix() }
	logLine := func(s string) { cfg.Log("%s", s) }
	tu := tickUsage{p: p, u: uenv, roster: lc.roster, own: tc.Cutover}
	view := p.agentView(cfg, tc.Cutover, tc.Terminals, tu)
	agents := func() []string { return p.tickAgents(tc.Terminals) }
	reconciler := &lifecycle.Reconciler{Q: q, Log: logLine, Ports: lifecycle.ReconcilerPorts{
		Agents:     agents,
		Registered: func() ([]string, error) { return paths.ReadAgents(home) },
		Observe: func(names []string, terminals map[string]string) map[string]session.Observed {
			if !tc.Terminals {
				return map[string]session.Observed{}
			}
			return p.observeTerminals(names, terminals)
		},
		NoteSeen:      func(map[string]session.Observed, time.Time) {},
		ResumeHandoff: func(_ db.Q, row db.HandoffRow) error { return lc.Mover.ResumeHandoff(row) },
		EnforceEdges:  func(q db.Q, agent string) error { _, err := env.EnforceEdges(q, agent); return err },
		WaitingFor:    func(q db.Q, agent string) (bool, error) { return lc.Manager.WaitingFor(agent) },
		WakeAgent: func(q db.Q, agent string) (bool, string, error) {
			return lc.Manager.WakeAgent(agent, lifecycle.WakeOpts{})
		},
		QueuedOrIdle:     func(q db.Q, agent, why string) (string, error) { return queuedOrIdle(q, agent, why) },
		RefreshView:      func(_ db.Q, agent string) { view.RefreshOne(agent) },
		LiftLogin:        liftLogin(team.Accounts{Home: home}),
		StillDrawn:       func(string) bool { return false },
		ResumableSession: func(q db.Q, agent string) string { return p.resumableSession(q, agent, tu) },
		Resolve: func(id int64, why string) error {
			_, _, err := env.Resolve(q, supervisor.ResolveIn{RID: id, Verdict: why, NoSettle: true})
			return err
		},
		Term: reconcileTerm{p: p, on: tc.Terminals},
		Run:  p.Run,
		Now:  now,
	}}
	pollUsage := func(db.Q) error { return nil } // the Python daemon's until the cutover
	if tc.Cutover {
		ctx, cancel := context.WithCancel(context.Background())
		go func() {
			<-tc.Stop
			cancel()
		}()
		poller := newPoller(ctx, cfg, uenv, q)
		pollUsage = func(db.Q) error { poller.Tick(ctx); return nil }
	}
	t := &tick.Tick{Q: q, Log: logLine, Now: now,
		Host:      tickHost{p: p, terminals: tc.Terminals},
		Usage:     tu,
		Delivery:  tickDelivery{env: env},
		Messaging: tickMessaging{env: env},
		Lifecycle: tickLifecycle{m: lc.Manager},
		Handoff:   tickHandoff{ho: lc.Handoff, mv: lc.Mover},
		Login:     loginPorts(team.Accounts{Home: home}),
		Reconcile: tickReconciler{r: reconciler},
		Sweeps: tick.Sweeps{
			BlockedSweep:    func(db.Q, int64) {},            // dialogs on a screen: a structured session has none
			CollectSnapshot: func(db.Q) error { return nil }, // a structured session has no screen to read
			PollUsage:       pollUsage,
			RelinkAccounts:  func() (map[string][]string, error) { return team.RelinkAll(home, logLine), nil },
			Rollover:        (&fold.Env{Home: home, Today: today(cfg)}).Rollover,
			Today:           today(cfg),
		},
		Cost:    &tick.CostPilot{Ports: costPorts{}},
		Observe: tickObserve{u: uenv, home: home, now: nowOf(cfg.Now), own: tc.Cutover},
		Config:  tickConfig{roster: lc.roster, set: lc.Manager.Settings},
	}
	t.Passes = append(p.passes(cfg, t, tc.Cutover), agentViewPass(view))
	if tc.Terminals {
		strays := &dangling.Sweep{Q: q, Home: home, Ports: danglingPorts{p: p}, Log: cfg.Log}
		t.Passes = append(t.Passes, tick.NamedSweep{Name: "dangling-terminals", Run: func(_ db.Q, now int64) error { return strays.Run(now) }})
	}
	dev := &tick.DevMode{Enabled: func() bool { return lc.Manager.Settings.Flag("dev_mode", false) }, Log: logLine, Now: nowOf(cfg.Now)}
	dev.Exe, _ = os.Executable()
	dev.Baseline()
	fds := &housekeeping.FDs{Log: logLine}
	scratch := &housekeeping.Scratch{Root: tc.ScratchRoot, Log: logLine}
	loop := &tick.Loop{Tick: t, Log: logLine, Stop: tc.Stop, Dev: dev, Housekeeping: []tick.NamedSweep{
		{Name: "fd check", Run: func(db.Q, int64) error { fds.Check(); return nil }},
		{Name: "log rotation", Run: func(db.Q, int64) error { return housekeeping.RotateLogs(home) }},
		{Name: "attachment sweep", Run: func(db.Q, int64) error { attachments.New(home).Sweep(); return nil }},
		{Name: "scratch sweep", Run: func(_ db.Q, now int64) error { scratch.Maybe(time.Unix(now, 0)); return nil }},
	}, Signalled: func() bool {
		select {
		case <-tc.Stop:
			return true
		default:
			return false
		}
	}}
	startup := func() {
		runStartup(logLine, startPorts{Reconcile: reconciler.Reconcile, StartWaiting: t.StartWaiting,
			Migrate: func() error { return env.MigrateOldMessages(q) }, Reload: func() error { return env.ReloadPending(q) }})
	}
	return &Tick{Tick: t, Loop: loop, dev: dev, Startup: startup}
}

// startPorts are the steps of the daemon's start.
type startPorts struct {
	Reconcile    func() ([]string, error)
	StartWaiting func() []string
	Migrate      func() error
	Reload       func() error
}

// runStartup is loop.py's start, in its order: reconcile; settle what could not be typed before the restart (an old say, a request told but
// never marked typed) so that it goes first, to agents already free; start the agents with work waiting and no session; retry every pending
// message. A reconcile that fails ends the start: starting agents on state it did not settle would act on a wrong picture.
func runStartup(log func(string), s startPorts) {
	log("daemon starting - reconciling")
	actions, err := s.Reconcile()
	for _, a := range actions {
		log("  " + a)
	}
	if err != nil {
		log(fmt.Sprintf("  reconcile: %v - the rest of the start is skipped", err))
		return
	}
	if err := s.Migrate(); err != nil {
		log(fmt.Sprintf("  migrate old messages: %v", err))
	}
	for _, a := range s.StartWaiting() {
		log("  " + a)
	}
	if err := s.Reload(); err != nil {
		log(fmt.Sprintf("  reload pending: %v", err))
	}
}

// Start runs the loop until Stop closes and returns a channel that closes when the tick in hand has finished. The daemon closes the
// database only after that: a close or a wake cut off by the process ending leaves a half write.
func (t *Tick) Start() (done <-chan struct{}) {
	d := make(chan struct{})
	go func() {
		defer close(d)
		if t.Startup != nil {
			t.Startup()
		}
		t.restarted = t.Loop.Run(nil)
	}()
	return d
}

// today is fold.today: the date on the daemon's clock, which names the day's digest.
func today(cfg Config) func() string {
	return func() string { return nowOf(cfg.Now)().Format("2006-01-02") }
}

func (p *Parts) structuredAgents() []string { return p.tickAgents(false) }

// tickAgents is the structured agents, and the terminal ones too when terminals is set.
func (p *Parts) tickAgents(terminals bool) []string {
	var out []string
	for _, a := range paths.ListAgents(p.home) {
		if terminals || p.Kind(a) {
			out = append(out, a)
		}
	}
	return out
}

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

// reconcileTerm is what the reconciler asks of a terminal. It reads no screen: a stalled agent's trust prompt and dialog count as gone,
// and a session is idle when its own file says so.
type reconcileTerm struct {
	p  *Parts
	on bool
}

func (t reconcileTerm) Available() bool {
	return t.on && t.p.LC != nil && t.p.LC.term.Backend.Available()
}
func (reconcileTerm) TrustPromptGone(string) bool        { return true }
func (reconcileTerm) ScreenBlocker(string) *term.Blocker { return nil }
func (t reconcileTerm) IdleNow(handle string) bool {
	agent := t.p.agentAt(handle)
	return agent != "" && t.p.sessionStatus(agent) == "idle"
}

// tickHost is tick.Host over the structured sessions and, with terminals, the terminal ones.
type tickHost struct {
	p         *Parts
	terminals bool
}

func (h tickHost) Agents() []string   { return h.p.tickAgents(h.terminals) }
func (h tickHost) Accounts() []string { return paths.ListAccounts(h.p.home) }

func (h tickHost) SessionAlive(agent string) bool {
	if h.terminals && !h.p.Kind(agent) {
		return h.p.sessionStatus(agent) != ""
	}
	return h.p.Run.Alive(agent)
}

// SessionIdle: the session is not mid-turn, and no permission prompt is open (a restart must not cut one short).
func (h tickHost) SessionIdle(agent string) bool {
	if h.terminals && !h.p.Kind(agent) {
		return h.p.sessionStatus(agent) == "idle"
	}
	return h.p.Run.Alive(agent) && !h.p.Run.Busy(agent) && !(h.p.Manager.Broker != nil && h.p.Manager.Broker.Has(agent))
}

// RunningAccount is the account the session was opened under; "" when unknown, which never contradicts the recorded account.
func (h tickHost) RunningAccount(agent string) string {
	if h.terminals && !h.p.Kind(agent) {
		if p := h.p; p.LC != nil {
			if _, acct, found := p.terminalSession(agent, p.LC.term.LiveTerminals()); found == sessionFound {
				return acct
			}
		}
		return ""
	}
	return h.p.OpenedOn(agent)
}

// resumableSession is the session id to remember for a closed agent: the one the daemon holds for a structured agent; for a terminal agent
// the one its statusline or session file gave, else the one stored.
func (p *Parts) resumableSession(q db.Q, agent string, tu tickUsage) string {
	if p.Kind(agent) {
		return p.Manager.SessionID(agent)
	}
	acct, _ := team.CurrentAccount(q, agent)
	if sid := tu.ReadForAgent(agent, acct).SessionID; sid != "" {
		return sid
	}
	if row, err := db.GetAgentState(q, agent); err == nil && row != nil {
		return row.ResumeSession.String
	}
	return ""
}

// sessionStatus is what the agent's terminal session says of itself, "" when none runs. A session in the file with no status reads "unknown".
func (p *Parts) sessionStatus(agent string) string {
	if p.LC == nil {
		return ""
	}
	rec, _, found := p.terminalSession(agent, p.LC.term.LiveTerminals())
	switch {
	case found == sessionNone:
		return ""
	case found == sessionUnknown || rec.Status == "":
		return "unknown" // a process runs, and what it says of itself cannot be read: alive, not idle
	}
	return rec.Status
}

// agentAt is the terminal agent whose terminal is handle.
func (p *Parts) agentAt(handle string) string {
	for _, a := range paths.ListAgents(p.home) {
		if row, err := db.GetAgentState(p.db, a); err == nil && row != nil && row.Terminal.String == handle {
			return a
		}
	}
	return ""
}

// observeTerminals is session/observe for the terminal agents: whether each one's terminal is listed and which session runs in it.
func (p *Parts) observeTerminals(names []string, terminals map[string]string) map[string]session.Observed {
	out := map[string]session.Observed{}
	if p.LC == nil {
		return out
	}
	live := p.LC.term.LiveTerminals() // nil: the host could not say, which is not "gone"
	for _, a := range names {
		if p.Kind(a) || terminals[a] == "" || live == nil {
			continue
		}
		alive := live[terminals[a]]
		o := session.Observed{TerminalAlive: &alive, SessionSeen: true}
		switch rec, _, found := p.terminalSession(a, live); found {
		case sessionFound:
			pid := rec.Pid
			o.SessionPID = &pid
		case sessionUnknown:
			o.SessionSeen = false // could not look: not "no session"
		}
		out[a] = o
	}
	return out
}

// tickUsage is tick.Usage over the usage package.
type tickUsage struct {
	p      *Parts
	u      *usage.Env
	roster *team.Roster
	own    bool // Cutover: the burn file is this daemon's to write
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
	st := statusOf(t.u.ReadForAgent(agent, acct))
	if acct == "" || t.p.LC == nil {
		return st
	}
	if !t.p.Kind(agent) {
		t.fillFromSession(agent, acct, &st)
		return st
	}
	// A structured session writes no status file: its session is the one the daemon holds, and its context is in the transcript the CLI keeps.
	st.SessionID = t.p.Manager.SessionID(agent)
	if st.SessionID == "" {
		if row, err := db.GetAgentState(t.p.db, agent); err == nil && row != nil {
			st.SessionID = row.ResumeSession.String
		}
	}
	if st.SessionID == "" {
		return st
	}
	st.TranscriptPath = t.p.LC.Handoff.FindTranscript(st.SessionID, acct)
	if used, model, ok := lastContext(st.TranscriptPath); ok {
		n := int(used)
		st.TrustedCtxUsed, st.ModelID = &n, model
	}
	return st
}

// fillFromSession completes a terminal agent's reading from its session file and transcript where the statusline gave none: the session
// id, and the context the newest reply says the window holds.
func (t tickUsage) fillFromSession(agent, acct string, st *tick.Status) {
	if st.SessionID == "" {
		if rec, _, found := t.p.terminalSession(agent, t.p.LC.term.LiveTerminals()); found == sessionFound {
			st.SessionID = rec.SessionID
		}
	}
	if st.SessionID == "" || st.TrustedCtxUsed != nil {
		return
	}
	if st.TranscriptPath == "" {
		st.TranscriptPath = t.p.LC.Handoff.FindTranscript(st.SessionID, acct)
	}
	if used, model, ok := lastContext(st.TranscriptPath); ok {
		n := int(used)
		st.TrustedCtxUsed, st.ModelID = &n, model
	}
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
	if !t.own {
		return
	}
	usage.WriteBurn(t.p.home, acct, rate, at)
}

// limitAt is when the limit the agent's last turn ended on was hit, ok false when it did not end on one.
func (t tickUsage) limitAt(q db.Q, agent string) (int64, bool) {
	at, err := handoff.AtLimit(q, agent)
	if err != nil || at == nil {
		return 0, false
	}
	switch v := at["at"].(type) {
	case int64:
		return v, true
	case float64:
		return int64(v), true
	}
	return 0, false
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
	ok, said, err := l.m.CloseAgent(agent, sessionOf(st), lifecycle.CloseOpts{Idle: true})
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

// tickHandoff is tick.Handoff: the note, the handoff itself and the move to another account.
type tickHandoff struct {
	ho *handoff.Env
	mv *lifecycle.Mover
}

func (h tickHandoff) SettleOwedClear(_ db.Q, agent string) bool {
	owed, _ := h.mv.SettleOwedClear(agent)
	return owed
}
func (h tickHandoff) Movable(kind string) bool { return lifecycle.MovableTrigger(kind) }
func (h tickHandoff) MovePlan(_ db.Q, agent, kind string) (string, string) {
	dest, why, err := h.mv.MovePlan(agent, kind, false)
	if err != nil {
		return "", err.Error()
	}
	return dest, why
}
func (h tickHandoff) MoveToRoom(_ db.Q, agent string, t tick.Trigger) bool {
	moved, _ := h.mv.MoveToRoom(agent, lifecycle.HandoffTrigger{Kind: t.Kind, Reason: t.Reason})
	return moved
}
func (h tickHandoff) PerformHandoff(_ db.Q, agent string, t tick.Trigger, dest string) bool {
	ok, err := h.mv.PerformHandoff(agent, lifecycle.HandoffTrigger{Kind: t.Kind, Reason: t.Reason}, dest)
	return ok && err == nil
}
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
	now  func() time.Time
	own  bool // Cutover: the usage poll is this daemon's; before it the Python daemon's, and these writers do nothing
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
	if !o.own {
		return nil
	}
	return o.u.Observe(q, acct, working, o.u.Read(acct), firsts)
}
func (o tickObserve) RecordUsageSample(q db.Q, acct string, _ tick.Status) error {
	if !o.own {
		return nil
	}
	return o.u.RecordSample(q, acct, o.u.Read(acct), o.now().Unix())
}
func (o tickObserve) PruneUsageSamples(q db.Q) error {
	if !o.own {
		return nil
	}
	_, err := o.u.PruneSamples(q)
	return err
}
func (o tickObserve) IngestTokens(q db.Q, acct string) error {
	if !o.own {
		return nil
	}
	_, err := o.u.IngestTokens(q, acct, usage.BytesPerTick)
	return err
}
func (o tickObserve) PruneTokens(q db.Q) error {
	if !o.own {
		return nil
	}
	_, err := o.u.PruneTokens(q)
	return err
}
func (o tickObserve) RefreshLongRatios(q db.Q) error {
	if !o.own {
		return nil
	}
	return o.u.RefreshLongRatios(q, o.now().Unix(), false)
}

// costPorts is tick.CostPorts: the prompt-cache clock of a session, read from the transcript the status names.
type costPorts struct{}

func (costPorts) ReadCache(transcriptPath string) *tick.CacheClock {
	c := handoff.ReadClock(transcriptPath)
	if c == nil {
		return nil
	}
	out := &tick.CacheClock{Left: func(now int64) *float64 {
		if left, ok := c.Left(float64(now)); ok {
			return &left
		}
		return nil
	}}
	if c.TTL != 0 {
		ttl := int64(c.TTL)
		out.TTL = &ttl
	}
	return out
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
