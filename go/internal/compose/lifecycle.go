package compose

import (
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/usage"
)

// This file builds the lifecycle manager over the structured sessions. A terminal agent is not served here: the terminal-shaped ports
// (the pane, the screen reads, the dialogs) answer as a terminal that is not there, and the Python daemon keeps those agents until the
// cutover. Everything a structured agent's start, wake, close and restart touch is real.

// Lifecycle is the lifecycle manager and what is built beside it.
type Lifecycle struct {
	Manager *lifecycle.Manager
	Spawns  *lifecycle.Spawns
	Launch  *lifecycle.Launcher
	Handoff *handoff.Env
}

// LifecycleConfig is what only the daemon main knows beyond Config.
type LifecycleConfig struct {
	// Env is the telling layer; it is the Delivery the lifecycle reads and the Heads the handoff record reads.
	Env *supervisor.Env
}

// Lifecycle builds the manager over p's runner. The database must be bound.
func (p *Parts) Lifecycle(cfg Config, lc LifecycleConfig) *Lifecycle {
	q, home := p.db, cfg.Home
	roster := team.NewRoster(home)
	uenv := &usage.Env{Home: home}
	accounts := team.Accounts{Home: home}
	launcher := &lifecycle.Launcher{Q: q, Home: home, Roster: roster, Now: cfg.Now, Ports: lifecycle.LaunchPorts{
		Usage:          &lifecycle.UsageAdapter{Env: uenv, Q: q},
		Accounts:       accounts,
		Vendors:        vendors{home: home, roster: roster},
		CurrentAccount: func(q db.Q, agent string) string { a, _ := team.CurrentAccount(q, agent); return a },
		// The reading is made on the database, not inside a call's transaction: the lifecycle is never called inside one.
		AccountReading: func(q db.Q, agent string) (string, string, bool) {
			r, ok, err := team.ReadAccount(q.(*sql.DB), agent, nil)
			if err != nil || !ok {
				return "", "", false
			}
			return r.Account, r.Source, true
		},
	}}
	launchPort := lifecycle.LaunchPort{L: launcher}
	ho := &handoff.Env{DB: q, Home: home, Heads: lc.Env, Now: cfg.Now,
		AgentSession: func(agent, account string) (string, string) {
			st := uenv.ReadForAgent(agent, account)
			return st.SessionID, st.Transcript
		}}
	spawns := &lifecycle.Spawns{Q: q, Host: noHost{}, Launch: launchPort, Delivery: lc.Env, Roster: roster, Now: cfg.Now,
		Run: p.Run, Locks: p.Locks}
	waiting := func(q db.Q, agent string) bool {
		head, err := lc.Env.EffectiveHead(q, agent)
		if err != nil || head != nil {
			return true // a read that fails says something may be waiting: the wake is cheap to refuse and dear to miss
		}
		return false
	}
	launcher.Ports.WaitingFor = waiting
	m := &lifecycle.Manager{Q: q, Home: home, Log: cfg.Log, Settings: &lifecycle.Settings{Home: home, Roster: roster},
		Delivery: lc.Env, Handoff: handoffPort{env: ho, q: q, now: nowOf(cfg.Now)}, Term: noTerminal{}, Blocked: noBlocked{}, Launch: launchPort, Spawn: spawner{inner: spawns, kind: p.Kind},
		Agents: agentsPort{home: home, roster: roster, trust: team.NewTrust(home), accounts: accounts, env: lc.Env, usage: uenv}, Status: &lifecycle.StatusAdapter{Env: uenv, Q: q, Structured: p.Kind},
		Observe: observer{p: p}, Typing: noTyping{}, Run: p.Run, Locks: p.Locks}
	p.LC = &Lifecycle{Manager: m, Spawns: spawns, Launch: launcher, Handoff: ho}
	return p.LC
}

func nowOf(f func() time.Time) func() time.Time {
	if f != nil {
		return f
	}
	return time.Now
}

// vendors is lifecycle.VendorsPort. A structured session has no driver: the Claude CLI is launched by the runner, not by a vendor's command line.
type vendors struct {
	home   string
	roster *team.Roster
}

func (v vendors) ForAgent(agent string) (lifecycle.Driver, error) {
	return nil, fmt.Errorf("%s: a vendor driver launches a terminal agent, which this daemon does not serve", agent)
}

func (v vendors) AgentVendor(agent string) string {
	cfg, err := v.roster.Read(agent)
	if err != nil {
		return team.DefaultVendor
	}
	vendor, _, _ := team.Parts(cfg)
	return vendor
}

func (v vendors) AccountVendor(acct string) string { return team.AccountVendor(v.home, acct) }

func (v vendors) ConfigDir(acct string) string {
	dir, err := team.AccountConfigDir(v.home, acct)
	if err != nil {
		return ""
	}
	return dir
}

var _ lifecycle.VendorsPort = vendors{}

// agentsPort is lifecycle.AgentsPort over the roster, the trust store and the accounts.
type agentsPort struct {
	home     string
	roster   *team.Roster
	trust    *team.Trust
	accounts team.Accounts
	env      *supervisor.Env
	usage    *usage.Env
}

var _ lifecycle.AgentsPort = agentsPort{}

func (a agentsPort) RepoOf(agent string) string { return a.roster.RepoOf(agent) }

func (a agentsPort) ReadConfig(agent string) error {
	_, err := a.roster.Read(agent)
	return err
}

func (a agentsPort) CurrentAccount(q db.Q, agent string) string {
	acct, _ := team.CurrentAccount(q, agent)
	return acct
}

func (a agentsPort) SkipPermissionsInEffect(agent string) (bool, error) {
	cfg, err := a.roster.Read(agent)
	if err != nil {
		return false, err
	}
	return a.roster.SkipPermissionsInEffect(agent, cfg), nil
}

func (a agentsPort) IsDeclared(folder string) bool { return a.trust.IsDeclared(folder) }

// MeasureBegin starts the cold-start measurement of a move: what the new account's usage was when the agent was put on it.
func (a agentsPort) MeasureBegin(q db.Q, agent, account string) {
	if err := a.usage.BeginColdStart(q, agent, account); err != nil {
		if a.env.Log != nil {
			a.env.Log("  %s: the cold-start measurement did not begin: %v", agent, err)
		}
	}
}

// RaiseDecision puts a decision in front of the founder about an agent: a task of theirs with the request as its card.
func (a agentsPort) RaiseDecision(q db.Q, agent, text, reason string) error {
	var after supervisor.After
	if _, err := a.env.RaiseDecision(q, &after, agent, text, reason, "", "", 0, 0); err != nil {
		return err
	}
	after.Run()
	return nil
}

func (a agentsPort) VendorName(agent string) string {
	cfg, err := a.roster.Read(agent)
	if err != nil {
		return team.DefaultVendor
	}
	vendor, _, _ := team.Parts(cfg)
	return vendor
}

// handoffPort is lifecycle.HandoffPort over the handoff record.
type handoffPort struct {
	env *handoff.Env
	q   db.Q
	now func() time.Time
}

var _ lifecycle.HandoffPort = handoffPort{}

func (h handoffPort) Capture(_ db.Q, agent string) (string, []string, error) {
	return h.env.Capture(agent, "")
}

func (h handoffPort) Record(_ db.Q, agent, closedSession string) ([]string, error) {
	return h.env.Record(agent, closedSession)
}

func (h handoffPort) MemoryLine(agent string) string {
	if h.env.MemoryLine == nil {
		return ""
	}
	return h.env.MemoryLine(agent)
}

func (h handoffPort) PreviousSession(_ db.Q, agent, account string) (string, string) {
	s, path, err := h.env.PreviousSession(agent, account, "")
	if err != nil {
		return "", ""
	}
	return s, path
}

func (h handoffPort) RequestLine(_ db.Q, agent string) string {
	line, err := h.env.RequestLine(agent)
	if err != nil {
		return ""
	}
	return line
}

// MovePlan: choosing another account for a walled agent belongs to the policy tick; until it lands an agent at a wall stays where it is.
func (h handoffPort) MovePlan(db.Q, string, string) (string, string) {
	return "", "moving to another account is the policy tick's, which this daemon does not run yet"
}

// BackingOff is daemon/handoff._handoff_backing_off: a handoff that keeps degrading is not retried every tick.
func (h handoffPort) BackingOff(q db.Q, agent string) bool {
	row, err := db.GetAgentState(q, agent)
	if err != nil || row == nil || row.ThrottleCount == 0 {
		return false
	}
	var last sql.NullInt64
	if q.QueryRow("SELECT MAX(updated) FROM handoff WHERE agent = ?", agent).Scan(&last) != nil || !last.Valid {
		return false
	}
	return h.now().Unix() < last.Int64+int64(states.BackoffFor(int(row.ThrottleCount)).Seconds())
}

func (h handoffPort) AnsweredTurn(_ db.Q, agent string) bool {
	ok, err := h.env.AnsweredTurn(agent)
	return err == nil && ok
}

func (h handoffPort) CacheClock(_ db.Q, agent, session string) (int64, int64, bool, bool) {
	c, err := h.env.CacheClock(agent, session)
	if err != nil || c == nil {
		return 0, 0, false, false
	}
	return int64(c.At), int64(c.TTL), c.TTL != 0, true
}

func (h handoffPort) CacheMargin(ttl int64) int64 { return int64(handoff.Margin(int(ttl))) }

func (h handoffPort) ClearNote(_ db.Q, agent string) { _ = h.env.ClearNote(agent) }

// observer is the process table of the structured sessions: the agents whose session this daemon has open, with its pid.
type observer struct{ p *Parts }

var _ lifecycle.Observer = observer{}

func (o observer) LiveSessions() (map[string]int, bool) {
	out := map[string]int{}
	for agent, pid := range o.p.Manager.Live() {
		out[agent] = pid
	}
	return out, true
}

// noTyping: a structured agent has no terminal to take, and nobody else can hold it.
type noTyping struct{}

func (noTyping) TakeTerminal(string) (func(), bool) { return func() {}, true }
func (noTyping) HeldByOther(db.Q, string) string    { return "" }
func (noTyping) HeldWhy(string, string) string      { return "" }

// noTerminal is the terminal a terminal agent would be in, which this daemon does not have.
type noTerminal struct{}

var _ lifecycle.Terminal = noTerminal{}

func (noTerminal) Available() bool                      { return false }
func (noTerminal) WaitIdle(string) bool                 { return false }
func (noTerminal) TypeableNow(string) bool              { return false }
func (noTerminal) ScreenBlocker(string) *term.Blocker   { return nil }
func (noTerminal) BackgroundWork(string) (string, bool) { return "", false }
func (noTerminal) TrustPromptShowing(string) bool       { return false }
func (noTerminal) TrustPromptFolder(string) string      { return "" }
func (noTerminal) ConfirmTrust(string) (bool, string)   { return false, notServed }
func (noTerminal) CloseSession(string) (bool, string)   { return false, notServed }
func (noTerminal) CloseTerminal(string) (bool, string)  { return false, notServed }
func (noTerminal) Relaunch(string, string, func() bool) (bool, string) {
	return false, notServed
}
func (noTerminal) ResumeWith(string, string) (bool, string) { return false, notServed }
func (noTerminal) BypassPromptShowing(string) bool          { return false }
func (noTerminal) AcceptBypass(string) (bool, string)       { return false, notServed }
func (noTerminal) WaitForTrustPrompt(string, bool)          {}
func (noTerminal) DriverScope() func()                      { return func() {} }

const notServed = "terminal agents are served by the Python daemon"

// noBlocked: a structured session has no dialogs to get past.
type noBlocked struct{}

var _ lifecycle.Blocked = noBlocked{}

func (noBlocked) NoteCloseFailure(db.Q, string)          {}
func (noBlocked) PastDialog(db.Q, string) (bool, string) { return false, notServed }
func (noBlocked) StopWorkAndExit(db.Q, string) bool      { return false }
func (noBlocked) AnswerByRule(db.Q, string, string) bool { return false }
func (noBlocked) AutoAcceptEnabled() bool                { return false }

// noHost is lifecycle.Host for a structured agent: there is no terminal to open and no worktree to make.
type noHost struct{}

var _ lifecycle.Host = noHost{}

func (noHost) Available() bool { return true }
func (noHost) Missing() string { return "" }

// CreateWorktree: a worktree is made through Orca, which the Go daemon does not drive; an agent that has one is woken in it, and a new
// one is the Python daemon's to spawn.
func (noHost) CreateWorktree(string, string, string) (string, string) {
	return "", "making a worktree is not served by this daemon yet"
}

// errNoWorktree is a start with nowhere to run: not counted against the agent.
var errNoWorktree = errors.New("making a worktree is not served by this daemon yet")

func (noHost) OpenTerminal(string, string, string) (string, string) { return "", notServed }
func (noHost) Manual(wt, repo, name, baseRef, command string) string {
	return fmt.Sprintf("git -C %s worktree add -b %s %s %s", repo, name, wt, baseRef)
}
func (noHost) WaitIdle(string, int) bool              { return false }
func (noHost) Submit(string, string, int)             {}
func (noHost) HasRuns() bool                          { return false }
func (noHost) RunCurrent() string                     { return "" }
func (noHost) RunExists(string) bool                  { return false }
func (noHost) RunUse(string)                          {}
func (noHost) RunCreate(string) string                { return "" }
func (noHost) TaskCreate(_, _, _, _, _ string) string { return "" }
func (noHost) Dispatch(_, _, _ string) string         { return "" }

// spawner is lifecycle.Spawner for what this daemon can start. A start it cannot do - a terminal agent, or a structured one with no
// worktree yet - is an error, which the lifecycle does not count against the agent: it is not a failed start, it was never tried.
type spawner struct {
	inner *lifecycle.Spawns
	kind  func(agent string) bool
}

var _ lifecycle.Spawner = spawner{}

func (s spawner) WorktreePath(worktree string) string { return s.inner.WorktreePath(worktree) }

func (s spawner) StartIn(agent, worktree, cmd string) (bool, string, error) {
	if !s.kind(agent) {
		return false, "", fmt.Errorf("%s: %s", agent, notServed)
	}
	return s.inner.StartIn(agent, worktree, cmd)
}

func (s spawner) Spawn(agent, repo string) (bool, string, error) {
	if !s.kind(agent) {
		return false, "", fmt.Errorf("%s: %s", agent, notServed)
	}
	// Spawn is for an agent with no worktree, and making one is Orca's.
	return false, "", fmt.Errorf("%s: %w", agent, errNoWorktree)
}
