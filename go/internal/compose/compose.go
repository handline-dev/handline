// Package compose builds the structured-session side of the daemon from its parts, in one place the daemon main wires: the structured
// manager, the transcript sink it emits to, the lifecycle runner and the supervisor's Agents port over it, and the one set of agent
// locks the lifecycle and the spawns share. It holds no behaviour of its own beyond adapting one interface to another.
package compose

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/transcript"
)

// Config is what the daemon main knows and the parts need.
type Config struct {
	DB   *sql.DB
	Home string
	Log  func(format string, args ...any)
	Now  func() time.Time
	// CLI is the claude binary.
	CLI string
	// ConfigDir is an account's CLAUDE_CONFIG_DIR; nil is the account's own directory under Home, "" the one the launch lands on.
	ConfigDir func(account string) string
	// Args are the extra CLI arguments of every structured launch (--setting-sources project and the hooks' --settings).
	Args []string
	// Env are NAME=value pairs set on every launch, besides the agent's name.
	Env []string
	// NoHooks starts a session without the hooks-only settings file (tests with no shared settings).
	NoHooks bool
	// Policy is the permission policy of an agent; nil allows nothing and guards nothing.
	Policy func(agent string) structured.Policy
	// Kind says whether an agent runs structured; nil reads agent.yaml's mode (KindFromYAML).
	Kind func(agent string) bool
	// ConfirmTimeout is how long a send waits for the CLI to echo it; zero is 30 s.
	ConfirmTimeout time.Duration
}

// Parts is what New builds. Sink goes to daemon.Config.Transcript, Kind to daemon.Config.Structured, Run to lifecycle.Manager.Run, Agents to
// supervisor.Ports.Agents, and Locks to lifecycle.Manager.Locks and Spawns.Locks alike.
type Parts struct {
	Manager *structured.Manager
	Sink    *transcript.Sink
	Run     *Runner
	Agents  *AgentsPort
	Locks   *lifecycle.AgentLocks
	Kind    func(agent string) bool
	// LC is the lifecycle, once Lifecycle has built it.
	LC *Lifecycle

	db   *sql.DB
	home string
	tell Telling
	log  func(format string, args ...any)
}

// Telling is what the supervisor's Env does with a session's end of a send: the operation is delivered once the CLI echoed it, and what was
// written and not echoed when the session ended is unknown (supervisor.Env).
type Telling interface {
	SessionConfirmed(q db.Q, agent, op string) error
	SessionLost(q db.Q, agent string) error
}

// events is the manager's event sink: the transcript sink, and the telling that a session ended.
type events struct {
	p *Parts
}

func (e events) Emit(agent string, ev structured.Event) {
	e.p.Sink.Emit(agent, ev)
	if ev.Type == "session_end" && e.p.tell != nil && e.p.db != nil {
		if err := e.p.tell.SessionLost(e.p.db, agent); err != nil && e.p.log != nil {
			e.p.log("  %s: telling the session's end failed: %v", agent, err)
		}
	}
}

// confirming tells the supervisor when the ledger records an echo.
type confirming struct {
	*structured.DBLedger
	p *Parts
}

func (c confirming) Put(o structured.Op) error {
	if err := c.DBLedger.Put(o); err != nil {
		return err
	}
	if o.State == structured.Confirmed && c.p.tell != nil {
		if err := c.p.tell.SessionConfirmed(c.p.db, c.DBLedger.Agent, o.ID); err != nil && c.p.log != nil {
			c.p.log("  %s: telling the echo of %s failed: %v", c.DBLedger.Agent, o.ID, err)
		}
	}
	return nil
}

// New builds the parts. The daemon fills in what only it knows when it starts (the sink's database, publisher and prompt reader).
func New(cfg Config) *Parts {
	kind := cfg.Kind
	if kind == nil {
		kind = KindFromYAML(cfg.Home)
	}
	run := &Runner{cfg: cfg, kind: kind}
	sink := &transcript.Sink{Now: cfg.Now}
	p := &Parts{Sink: sink, Run: run, Locks: &lifecycle.AgentLocks{}, Kind: kind, log: cfg.Log, home: cfg.Home}
	broker := &structured.Broker{Policy: cfg.Policy, Events: sink}
	mgr := &structured.Manager{Broker: broker, Events: events{p}, Now: cfg.Now}
	run.mgr, run.parts = mgr, p
	sink.Session = mgr.SessionID
	p.Manager, p.Agents = mgr, &AgentsPort{mgr: mgr, kind: kind}
	if cfg.DB != nil {
		p.Bind(cfg.DB, nil)
	}
	return p
}

// Bind gives the parts the database when it is open (the sends' ledger, the permission prompt rows, the transcript's events) and the
// telling that follows a session's echoes and its end; a nil tell tells nothing.
func (p *Parts) Bind(db *sql.DB, tell Telling) {
	p.db, p.tell = db, tell
	p.Manager.DB, p.Sink.DB = db, db
	if p.Manager.Broker != nil {
		p.Manager.Broker.Store = &structured.DBPrompts{DB: db, Now: p.Manager.Now}
	}
}

// KindFromYAML reads an agent's kind from its agent.yaml: a top-level `mode: structured`. Any other value, or none, is a terminal agent.
func KindFromYAML(home string) func(agent string) bool {
	return func(agent string) bool {
		b, err := os.ReadFile(filepath.Join(paths.AgentsDir(home), agent, "agent.yaml"))
		if err != nil {
			return false
		}
		for _, line := range strings.Split(string(b), "\n") {
			if v, ok := strings.CutPrefix(line, "mode:"); ok {
				v, _, _ = strings.Cut(v, "#")
				return strings.Trim(strings.TrimSpace(v), `"'`) == "structured"
			}
		}
		return false
	}
}

// Runner is lifecycle.Runner over the structured manager. It turns a StartSpec into the CLI's launch.
type Runner struct {
	cfg   Config
	kind  func(agent string) bool
	mgr   *structured.Manager
	parts *Parts
}

var _ lifecycle.Runner = (*Runner)(nil)

func (r *Runner) Structured(agent string) bool { return r.kind(agent) }

// Open starts agent's CLI in the spec's worktree and returns once it has answered initialize.
func (r *Runner) Open(spec lifecycle.StartSpec) error {
	cfgDir := ""
	if r.cfg.ConfigDir != nil {
		cfgDir = r.cfg.ConfigDir(spec.Account)
	} else if spec.Account != "" {
		cfgDir, _ = team.AccountConfigDir(r.cfg.Home, spec.Account)
	}
	args := r.cfg.Args
	if !r.cfg.NoHooks {
		path, err := WriteHooksSettings(r.cfg.Home, spec.Agent)
		if err != nil {
			return fmt.Errorf("%s: %w", spec.Agent, err)
		}
		args = append(append([]string{}, args...), "--settings", path)
	}
	l := structured.Launch{Path: r.cfg.CLI, Args: args, Cwd: spec.Worktree, ConfigDir: cfgDir, SessionID: spec.SessionID,
		Resume: spec.Resume, Env: append([]string{"OCP_AGENT=" + spec.Agent}, r.cfg.Env...)}
	if r.parts.db != nil {
		l.Ledger = confirming{&structured.DBLedger{DB: r.parts.db, Agent: spec.Agent, Now: r.cfg.Now}, r.parts}
	}
	_, err := r.mgr.Start(context.Background(), spec.Agent, l)
	return err
}

func (r *Runner) Alive(agent string) bool { return r.mgr.Has(agent) }

func (r *Runner) Busy(agent string) bool {
	s := r.mgr.Get(agent)
	return s != nil && s.ExitErr() == nil && s.Busy()
}

// Send writes text and returns once the CLI has echoed it: an error is a message not delivered.
func (r *Runner) Send(agent, op, text string) error {
	if _, err := r.mgr.Send(agent, op, text, structured.Now); err != nil {
		return err
	}
	timeout := r.cfg.ConfirmTimeout
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	if s := r.mgr.Get(agent); s != nil {
		return s.WaitConfirmed(ctx, op)
	}
	return fmt.Errorf("%s has no structured session", agent)
}

func (r *Runner) Interrupt(agent string) error {
	s := r.mgr.Get(agent)
	if s == nil {
		return fmt.Errorf("%s has no structured session", agent)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return s.Interrupt(ctx)
}

func (r *Runner) RetryingSince(agent string) int64 { return r.mgr.RetryingSince(agent) }

func (r *Runner) Close(agent string) error {
	if r.mgr.Get(agent) == nil {
		return nil
	}
	return r.mgr.Stop(agent)
}

// WaitIdle polls the session's busy flag: the turn ended when it is clear.
func (r *Runner) WaitIdle(agent string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		if !r.Busy(agent) {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// AgentsPort is supervisor.Agents over the structured manager.
type AgentsPort struct {
	mgr  *structured.Manager
	kind func(agent string) bool
}

var _ supervisor.Agents = (*AgentsPort)(nil)

func (a *AgentsPort) Structured(agent string) bool { return a.kind(agent) }
func (a *AgentsPort) Live(agent string) bool       { return a.mgr.Has(agent) }
func (a *AgentsPort) Send(agent, op, text string, d structured.Delivery) (structured.Receipt, error) {
	return a.mgr.Send(agent, op, text, d)
}
