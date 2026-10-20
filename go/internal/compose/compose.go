// Package compose builds the daemon's agent side from its parts, in one place the daemon main wires: the lifecycle over the terminal
// host, the supervisor's ports, the policy tick and the one set of agent locks the lifecycle and the spawns share. It holds no behaviour
// of its own beyond adapting one interface to another.
package compose

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/heal"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sysres"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/turns"
)

// Config is what the daemon main knows and the parts need.
type Config struct {
	DB   *sql.DB
	Home string
	Log  func(format string, args ...any)
	Now  func() time.Time
	// CLI is the claude binary the usage poll and the plan reads run.
	CLI string
	// Env are NAME=value pairs set on every claude the daemon runs.
	Env []string
	// Peer says whether a terminal agent's messages go through its session's peer inbox; nil reads agent.yaml's peer_inbox (PeerFromYAML).
	Peer func(agent string) bool
	// Term is the terminal host agents run in; nil is the one config.yaml names (tmux by default). Tests give a fake one.
	Term *term.Term
	// Turns, when set, is asked before the screen whether a terminal agent is mid-turn (-turns-from-hooks).
	Turns *turns.Tracker
	// DiskFree is the free and total bytes of the volume holding a path, for the disk watch; nil is statfs. Tests give a low one.
	DiskFree func(path string) (free, total int64, err error)
	// Machine is the machine's latest measure for the load gate (sysres.Sampler.LatestMachine); nil checks only the agent cap.
	Machine func() *sysres.Machine
	// NotifiedNeedsYou has an unanswered prompt that Claude Code's Notification hook reports raise the needs-you card.
	NotifiedNeedsYou bool
	// FounderTyped has the founder's own messages typed at a terminal agent as its person's prompt (supervisor.Env.FounderTyped); needs
	// Turns.
	FounderTyped bool
}

// Parts is what New builds. Locks goes to lifecycle.Manager.Locks and Spawns.Locks alike.
type Parts struct {
	Locks *lifecycle.AgentLocks
	Peer  func(agent string) bool
	// LC is the lifecycle, once Lifecycle has built it.
	LC *Lifecycle

	db       *sql.DB
	home     string
	notified bool // Config.NotifiedNeedsYou
	turns    *turns.Tracker
	tell     Telling
	log      func(format string, args ...any)
	healer   *heal.Healer // the tick's, once Tick has built it
}

// Wait returns once the heal steps still running off the tick have ended: the daemon closes the database only after.
func (p *Parts) Wait() {
	if p.healer != nil {
		p.healer.Wait()
	}
}

// Telling is what the supervisor's Env does with a session's end of a send: the operation is delivered once the session showed it, and
// what was written and not shown when the session ended is unknown (supervisor.Env).
type Telling interface {
	SessionWritten(q db.Q, agent, op string) error
	SessionConfirmed(q db.Q, agent, op string) error
	SessionLost(q db.Q, agent string) error
}

// New builds the parts. The daemon fills in what only it knows when it starts (the database and the telling).
func New(cfg Config) *Parts {
	peer := cfg.Peer
	if peer == nil {
		peer = PeerFromYAML(cfg.Home)
	}
	p := &Parts{Locks: &lifecycle.AgentLocks{}, Peer: peer, log: cfg.Log, home: cfg.Home, notified: cfg.NotifiedNeedsYou, turns: cfg.Turns}
	if cfg.DB != nil {
		p.Bind(cfg.DB, nil)
	}
	return p
}

// Bind gives the parts the database when it is open and the telling that follows a session's deliveries and its end; a nil tell tells
// nothing.
func (p *Parts) Bind(db *sql.DB, tell Telling) {
	p.db, p.tell = db, tell
}

// yamlFlag reads a top-level `key: true` of an agent's agent.yaml; no such line, or no file, is def, and any other value is false.
func yamlFlag(home, key string, def bool) func(agent string) bool {
	return func(agent string) bool {
		b, err := os.ReadFile(filepath.Join(paths.AgentsDir(home), agent, "agent.yaml"))
		if err != nil {
			return def
		}
		for _, line := range strings.Split(string(b), "\n") {
			if v, ok := strings.CutPrefix(line, key+":"); ok {
				v, _, _ = strings.Cut(v, "#")
				return strings.EqualFold(strings.Trim(strings.TrimSpace(v), `"'`), "true")
			}
		}
		return def
	}
}
