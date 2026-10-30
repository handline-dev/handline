package compose

import (
	"sort"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/heal"
	"github.com/handline/handline/go/internal/hrwrites"
	"github.com/handline/handline/go/internal/keepawake"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/schedules"
	"github.com/handline/handline/go/internal/sparenotice"
	"github.com/handline/handline/go/internal/subscriptions"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
	"github.com/handline/handline/go/internal/usage"
)

// passes are the guarded passes the tick makes after its per-agent turns, in daemon/evaluate.py's order: the timebox, the notices, the stuck waits,
// the settings a launch read, and last the healing of stuck agents. A structured session records no launch parts yet, so applying changed
// settings finds nothing recorded and restarts nobody until it does.
func (p *Parts) passes(cfg Config, tk *tick.Tick, cutover bool) []tick.NamedSweep {
	lc := p.LC
	timebox := &tick.Timebox{Ports: timeboxPorts{p: p, env: lc.Env, set: lc.Manager.Settings, home: cfg.Home}, Cutover: cutover}
	wait := &tick.WaitFor{Decide: waitDecider{lc: lc}, Log: func(s string) { cfg.Log("%s", s) }}
	apply := &tick.ApplySettings{Ports: settingsPorts{launcher: lc.Launch}}
	subs := &subscriptions.Env{DB: lc.Env.DB, Home: cfg.Home, Asker: founderAsker{lc.Env}, Tasks: founderCloser{lc.Env, cfg.Home},
		Plans: vendorPlans{cfg.Home, cfg.CLI}, Now: cfg.Now, Log: cfg.Log}
	sched := &schedules.Runner{Q: p.db, Agents: func() []string { return paths.ListAgents(cfg.Home) }, Schedules: (&reads.Env{DB: p.db, Home: cfg.Home}).SchedulesOf,
		Say: lc.Env.SystemSayThen, Log: func(s string) { cfg.Log("%s", s) }}
	healer := &heal.Healer{Ports: healPorts{lc: lc, home: cfg.Home, needs: p.needsYou()}, Log: func(s string) { cfg.Log("%s", s) }}
	p.healer = healer
	held := &hrwrites.Env{DB: lc.Env.DB, Home: cfg.Home, Say: lc.Env, Log: cfg.Log,
		Ask: func(title, body string, tags ...string) (int64, error) {
			return taskwrites.AskFounder(lc.Env.DB, lc.Env, title, body, tags...)
		}}
	if lc.Env.Observer != nil {
		held.Live = lc.Env.Observer.LiveSessions
	}
	spare := p.spareNotice(cfg)
	awake := &keepawake.Holder{Log: func(s string) { cfg.Log("%s", s) }}
	return []tick.NamedSweep{
		{Name: "keep-awake", Run: func(q db.Q, now int64) error {
			awake.Sync(keepawake.ModeOf(usage.LoadConfig(cfg.Home)), lc.Gate.Active() > 0)
			return nil
		}},
		{Name: "timebox", Run: func(q db.Q, now int64) error { return timebox.Sweep(tk, now) }},
		{Name: "notice-digest", Run: fromCutover(cutover, func(q db.Q, now int64) error { return lc.Env.SweepNotices(now) })},
		{Name: "task-queue", Run: fromCutover(cutover, func(q db.Q, now int64) error { return lc.Env.SweepTaskQueue(now) })},
		{Name: "task-holds", Run: fromCutover(cutover, func(q db.Q, now int64) error { return lc.Env.SweepHolds(now) })},
		{Name: "said-and-stopped", Run: fromCutover(cutover, func(db.Q, int64) error { return lc.Env.SweepSaidAndStopped() })},
		{Name: "subscriptions", Run: fromCutover(cutover, func(q db.Q, now int64) error { return subs.Sweep() })},
		{Name: "wait-for", Run: fromCutover(cutover, func(q db.Q, now int64) error { return wait.Sweep(q, now) })},
		{Name: "schedules", Run: fromCutover(cutover, func(q db.Q, now int64) error { return sched.Sweep(now) })},
		{Name: "apply-settings", Run: func(q db.Q, now int64) error { return apply.Sweep(tk, now) }},
		{Name: "heal", Run: fromCutover(cutover, healer.Sweep)},
		{Name: "spare-notice", Run: fromCutover(cutover, func(q db.Q, now int64) error { return spare.Sweep(now) })},
		{Name: "hr-held", Run: func(q db.Q, now int64) error {
			return held.SweepHeld(heldPorts(tk, q, healPorts{lc: lc, home: cfg.Home}))
		}},
		{Name: "left-worktrees", Run: func(db.Q, int64) error { return held.SweepLeftWorktrees() }},
	}
}

// heldPorts are the tick's session calls for a roster change held until a turn ends: a session is closed only at its prompt.
func heldPorts(tk *tick.Tick, q db.Q, h healPorts) hrwrites.HeldPorts {
	return hrwrites.HeldPorts{
		Interrupt: h.Interrupt,
		Alive:     tk.Host.SessionAlive,
		Idle:      func(a string) bool { return tk.Delivery.SessionPresent(a) && tk.Host.SessionIdle(a) },
		Close: func(a string) (bool, string) {
			acct, _ := team.CurrentAccount(q, a)
			return tk.Lifecycle.CloseAgent(q, a, tk.Usage.ReadForAgent(a, acct))
		},
		Start: func(a string) (bool, string) { return tk.Lifecycle.WakeAgent(q, a) },
	}
}

// fromCutover runs a pass that reads and writes state Python's sweep of the same name also keeps, only once Python's is off: two daemons
// over one cursor or meta key overwrite each other and tell the founder twice.
func fromCutover(cutover bool, run func(q db.Q, now int64) error) func(q db.Q, now int64) error {
	if cutover {
		return run
	}
	return func(db.Q, int64) error { return nil }
}

// waitDecider raises a stuck wait to the founder as a decision.
type waitDecider struct{ lc *Lifecycle }

func (w waitDecider) RaiseDecision(q db.Q, who, text, source string) error {
	var after supervisor.After
	if _, err := w.lc.Env.RaiseDecision(q, &after, who, text, source, "", "", 0, 0); err != nil {
		return err
	}
	after.Run()
	return nil
}

// settingsPorts is tick.SettingsPorts: what the next launch would read, named, in the order a person reads them.
type settingsPorts struct {
	launcher interface {
		FingerprintParts(agent string) (map[string]string, error)
	}
}

func (s settingsPorts) FingerprintParts(agent string) ([]tick.Part, error) {
	m, err := s.launcher.FingerprintParts(agent)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(m))
	for k := range m {
		names = append(names, k)
	}
	sort.Strings(names)
	out := make([]tick.Part, len(names))
	for i, k := range names {
		out[i] = tick.Part{Name: k, Value: m[k]}
	}
	return out, nil
}

func (settingsPorts) CurrentAccount(q db.Q, agent string) string {
	a, _ := team.CurrentAccount(q, agent)
	return a
}

// timeboxPorts is tick.TimeboxPorts over the board, the supervisor's background bookkeeping and the idle-close settings.
type timeboxPorts struct {
	p   *Parts
	env *supervisor.Env
	set interface {
		CloseTimeoutS(agent string) int64
		Never() int64
		BgNoticeS(agent string) int64
		BgGraceS(agent string) int64
		BgCheckS(agent string) int64
	}
	home string
}

func (t timeboxPorts) TaskDoing(q db.Q, task *db.Task) bool {
	state, err := db.TaskState(q, task)
	return err == nil && state == "doing"
}
func (t timeboxPorts) SupervisorOf(agent string) string {
	tm, err := team.LoadTeam(t.home)
	if err != nil || tm == nil {
		return ""
	}
	return tm.SupervisorOf(agent)
}

func (t timeboxPorts) SendNotice(q db.Q, agent, text string) bool {
	sent, err := t.env.TypeNotice(q, agent, text)
	return err == nil && sent
}
func (t timeboxPorts) SendBatch(q db.Q, agent, text string) error {
	_, err := t.env.Notice(q, agent, text, "timebox")
	return err
}
func (t timeboxPorts) BgStale(q db.Q, agent string, now int64) bool {
	ok, _ := t.env.BgStale(q, agent, now)
	return ok
}
func (t timeboxPorts) BgOwesNothing(q db.Q, agent string) bool {
	ok, _ := t.env.BgOwesNothing(q, agent)
	return ok
}
func (t timeboxPorts) BgSilentS(q db.Q, agent string, now int64) (int64, bool) {
	s, ok, _ := t.env.BgSilentS(q, agent, now)
	return s, ok
}
func (t timeboxPorts) BgSince(q db.Q, agent string) (int64, bool) {
	s, ok, _ := t.env.BgSince(q, agent)
	return s, ok
}
func (t timeboxPorts) BgPromptedAt(q db.Q, agent string) int64 {
	at, _ := t.env.BgPromptedAt(q, agent)
	return at
}
func (t timeboxPorts) StaleBgVetoS() int64 { return supervisor.StaleBgVetoS }
func (t timeboxPorts) ForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) *tick.Forgotten {
	f, err := t.env.ForgottenResolve(q, agent, row, now)
	if err != nil || f == nil {
		return nil
	}
	out := &tick.Forgotten{HeadID: f.Head.ID, IdleS: f.IdleS, Behind: f.Behind}
	if tasks, err := db.TasksForRequest(q, f.Head.ID); err == nil && len(tasks) > 0 {
		out.Task = tasks[0]
	}
	out.Blocked, _ = t.env.HeadTaskBlocked(q, agent, f.Head)
	out.Exhausted, _ = t.env.NudgeExhausted(q, agent, f.Head.ID)
	return out
}
func (t timeboxPorts) NudgeScheduleLen() int { return len(supervisor.NudgeScheduleS) }
func (t timeboxPorts) NeverCloses(agent string) bool {
	return t.set.CloseTimeoutS(agent) == t.set.Never()
}
func (t timeboxPorts) BgNoticeS(agent string) int64 { return t.set.BgNoticeS(agent) }
func (t timeboxPorts) BgGraceS(agent string) int64  { return t.set.BgGraceS(agent) }
func (t timeboxPorts) BgCheckS(agent string) int64  { return t.set.BgCheckS(agent) }

// BgJobs names what the agent's last Stop said still runs, the last count of them when count is set.
func (t timeboxPorts) BgJobs(_ db.Q, agent string, count int, _ int64) []string {
	if t.p.turns == nil {
		return nil
	}
	jobs := t.p.turns.Running(agent)
	if count > 0 && len(jobs) > count {
		jobs = jobs[len(jobs)-count:]
	}
	return jobs
}

// spareNotice is the sweep that tells the founder of a weekly loss coming, and offers the spare tasks to use it.
func (p *Parts) spareNotice(cfg Config) *sparenotice.Env {
	lc, home := p.LC, cfg.Home
	now := func() time.Time {
		if cfg.Now != nil {
			return cfg.Now()
		}
		return time.Now()
	}
	return &sparenotice.Env{Q: p.db, Now: now, Log: cfg.Log,
		Accounts: func() []string {
			var out []string
			for _, a := range paths.ListAccounts(home) {
				if lc.usage.NotFor(a, team.DefaultVendor) == "" {
					out = append(out, a)
				}
			}
			return out
		},
		Forecast: func(acct string) (usage.Forecast, bool) {
			rows, err := usage.SamplesSince(p.db, now().Unix()-86400-usage.OutlookMaxAgeS)
			if err != nil {
				return usage.Forecast{}, false
			}
			return lc.usage.ForecastFor(p.db, acct, rows)
		},
		ResetAt: func(acct string) int64 {
			left, _ := usage.WeekEndsIn(lc.usage.Read(acct), float64(now().Unix()))
			return now().Unix() + int64(left)
		},
		Waiting: func(acct string) bool {
			for _, agent := range paths.ListAgents(home) {
				if cur, _ := team.CurrentAccount(p.db, agent); cur == acct {
					if n, err := db.UnreachedCount(p.db, agent); err == nil && n > 0 {
						return true
					}
				}
			}
			return false
		},
		Ask: func(title, body string, tags ...string) (int64, error) {
			return taskwrites.AskFounder(p.db, lc.Env, title, body, tags...)
		},
		Tell: func(text string) error { return lc.Env.SystemSay(db.NoticeRequester, "handline", text) },
	}
}
