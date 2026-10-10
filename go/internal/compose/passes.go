package compose

import (
	"os"
	"path/filepath"
	"sort"
	"strconv"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// passes are the guarded passes the tick makes after its per-agent turns, in daemon/evaluate.py's order: the notices, the stuck waits and
// the settings a launch read. A structured session records no launch parts yet, so applying changed settings finds nothing recorded and
// restarts nobody until it does.
func (p *Parts) passes(cfg Config, tk *tick.Tick, cutover bool) []tick.NamedSweep {
	lc := p.LC
	timebox := &tick.Timebox{Ports: timeboxPorts{p: p, env: lc.Env, set: lc.Manager.Settings, home: cfg.Home}, Cutover: cutover}
	wait := &tick.WaitFor{Decide: waitDecider{lc: lc}, Log: func(s string) { cfg.Log("%s", s) }}
	apply := &tick.ApplySettings{Ports: settingsPorts{launcher: lc.Launch}}
	return []tick.NamedSweep{
		{Name: "timebox", Run: func(q db.Q, now int64) error { return timebox.Sweep(tk, now) }},
		{Name: "task-queue", Run: fromCutover(cutover, func(q db.Q, now int64) error { return lc.Env.SweepTaskQueue(now) })},
		{Name: "task-holds", Run: fromCutover(cutover, func(q db.Q, now int64) error { return lc.Env.SweepHolds(now) })},
		{Name: "wait-for", Run: fromCutover(cutover, func(q db.Q, now int64) error { return wait.Sweep(q, now) })},
		{Name: "apply-settings", Run: func(q db.Q, now int64) error { return apply.Sweep(tk, now) }},
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
func (t timeboxPorts) TaskBegun(q db.Q, tid int64) bool { ok, _ := db.Begun(q, tid); return ok }
func (t timeboxPorts) SupervisorOf(agent string) string {
	tm, err := team.LoadTeam(t.home)
	if err != nil || tm == nil {
		return ""
	}
	return tm.SupervisorOf(agent)
}
func (t timeboxPorts) ChildWait(q db.Q, agent string) bool {
	w, err := db.ChildWait(q, agent)
	return err == nil && w != ""
}

// StopWaitReason is actions.stop_wait_reason: why the agent should not stop now - it holds a current task with no recorded wait.
func (t timeboxPorts) StopWaitReason(q db.Q, agent string) string {
	tid, has, err := db.CurrentTask(q, agent)
	if err != nil || !has {
		return ""
	}
	if kids, err := db.OpenChildTasks(q, tid); err != nil || len(kids) > 0 {
		return ""
	}
	if blockers, err := db.TaskBlockers(q, tid); err != nil || len(blockers) > 0 {
		return ""
	}
	var open int
	if q.QueryRow("SELECT COUNT(*) FROM tasks t JOIN task_requests l ON l.task_id = t.id JOIN requests r ON r.id = l.request_id "+
		"WHERE t.owner = ? AND t.opened_by = ? AND t.discarded_by IS NULL AND r.state = 'queued'", db.Human, agent).Scan(&open) != nil || open > 0 {
		return ""
	}
	if t.proposalWaiting(agent) {
		return "" // its roster proposal is waiting on the founder
	}
	return "T" + strconv.FormatInt(tid, 10) + " is current with no recorded wait"
}

// proposalWaiting is whether a roster proposal the agent made still waits on the founder: hr/*.yaml with requested_by and state proposed.
func (t timeboxPorts) proposalWaiting(agent string) bool {
	files, _ := filepath.Glob(filepath.Join(t.home, "hr", "*.yaml"))
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			continue
		}
		doc, err := yamlcompat.Parse(string(raw))
		m, ok := doc.(map[string]any)
		if err != nil || !ok {
			continue
		}
		if by, _ := m["requested_by"].(string); by == agent {
			if state, _ := m["state"].(string); state == "proposed" {
				return true
			}
		}
	}
	return false
}

func (t timeboxPorts) SendNotice(q db.Q, agent, text string) bool {
	sent, err := t.env.TypeNotice(q, agent, text)
	return err == nil && sent
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

// BgJobs: a structured session shows no footer to count its background jobs from, so none are named.
func (t timeboxPorts) BgJobs(db.Q, string, int, int64) []string { return nil }
