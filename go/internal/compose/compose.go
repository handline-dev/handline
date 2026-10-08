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
	"sync"
	"time"

	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
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
	// ConfigDir is an account's CLAUDE_CONFIG_DIR; "" for the one the launch lands on.
	ConfigDir func(account string) string
	// Args are the extra CLI arguments of every structured launch (--setting-sources project and the hooks' --settings).
	Args []string
	// Env are NAME=value pairs set on every launch, besides the agent's name.
	Env []string
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
}

// New builds the parts. The daemon fills in what only it knows when it starts (the sink's database, publisher and prompt reader).
func New(cfg Config) *Parts {
	kind := cfg.Kind
	if kind == nil {
		kind = KindFromYAML(cfg.Home)
	}
	run := &Runner{cfg: cfg, kind: kind, sessions: map[string]string{}}
	sink := &transcript.Sink{Now: cfg.Now, Session: run.sessionOf}
	broker := &structured.Broker{Policy: cfg.Policy, Events: sink}
	mgr := &structured.Manager{Broker: broker, Events: sink, Now: cfg.Now}
	run.mgr = mgr
	p := &Parts{Manager: mgr, Sink: sink, Run: run, Agents: &AgentsPort{mgr: mgr, kind: kind}, Locks: &lifecycle.AgentLocks{}, Kind: kind}
	if cfg.DB != nil {
		p.Bind(cfg.DB)
	}
	return p
}

// Bind gives the parts the database when it is open: the sends' ledger, the permission prompt rows and the transcript's events.
func (p *Parts) Bind(db *sql.DB) {
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
	cfg  Config
	kind func(agent string) bool
	mgr  *structured.Manager

	mu       sync.Mutex
	sessions map[string]string // agent -> the id of the session last opened
}

var _ lifecycle.Runner = (*Runner)(nil)

func (r *Runner) Structured(agent string) bool { return r.kind(agent) }

func (r *Runner) sessionOf(agent string) string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.sessions[agent]
}

// Open starts agent's CLI in the spec's worktree and returns once it has answered initialize.
func (r *Runner) Open(spec lifecycle.StartSpec) error {
	cfgDir := ""
	if r.cfg.ConfigDir != nil {
		cfgDir = r.cfg.ConfigDir(spec.Account)
	}
	l := structured.Launch{Path: r.cfg.CLI, Args: r.cfg.Args, Cwd: spec.Worktree, ConfigDir: cfgDir, SessionID: spec.SessionID,
		Resume: spec.Resume, Env: append([]string{"OCP_AGENT=" + spec.Agent}, r.cfg.Env...)}
	// The id is known before the CLI answers, so the first events of the session already carry it.
	r.mu.Lock()
	prev, had := r.sessions[spec.Agent]
	r.sessions[spec.Agent] = spec.SessionID
	r.mu.Unlock()
	if _, err := r.mgr.Start(context.Background(), spec.Agent, l); err != nil {
		r.mu.Lock() // a start that did not happen leaves the running session's id as it was
		if had {
			r.sessions[spec.Agent] = prev
		} else {
			delete(r.sessions, spec.Agent)
		}
		r.mu.Unlock()
		return err
	}
	return nil
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
