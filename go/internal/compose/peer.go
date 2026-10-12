package compose

import (
	"context"
	"database/sql"
	"errors"
	"slices"
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/peerport"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
)

// PeerKey is the top-level agent.yaml key that has a terminal agent's messages delivered through its session's peer inbox. On unless the
// agent's agent.yaml says `peer_inbox: false`: nothing types at a terminal any more, so the inbox is the only way in.
const PeerKey = "peer_inbox"

// PeerFromYAML reads PeerKey from an agent's agent.yaml.
func PeerFromYAML(home string) func(agent string) bool { return yamlFlag(home, PeerKey, true) }

// settlePeerSends is the start of a daemon: the peer port's queue is in memory, so what it had taken for a terminal agent and not seen in the
// transcript when the last daemon stopped is unknown now, as a message lost with its session is, and its sender is told.
func (p *Parts) settlePeerSends() {
	if p.Peer == nil || p.db == nil || p.tell == nil {
		return
	}
	for _, agent := range paths.ListAgents(p.home) {
		if p.Kind(agent) || !p.Peer(agent) {
			continue
		}
		if err := p.tell.SessionLost(p.db, agent); err != nil && p.log != nil {
			p.log("  %s: settling the messages the last daemon left: %v", agent, err)
		}
	}
}

// peerDelivery is the Telling the peer port reports to.
type peerDelivery interface {
	SessionConfirmed(q db.Q, agent, op string) error
	SessionNotDelivered(agent, op, why string, written bool) error
	SessionLost(q db.Q, agent string) error
}

// NewPeerPort is the Agents port of the terminal agents that peer reports through: the session is the live one in the agent's worktree under
// its account, a message is delivered once its transcript shows it, and the outcome goes to tell.
func NewPeerPort(home string, d *sql.DB, peer func(agent string) bool, tell peerDelivery, log func(string, ...any)) *peerport.Port {
	return &peerport.Port{
		Peer:   peer,
		Find:   func(agent string) (peerinbox.Session, error) { return findSession(home, d, agent) },
		Sender: peerinbox.New(),
		From:   peerFrom(home, d),
		Ctx:    context.Background(),
		OnDelivered: func(agent, op string) {
			if err := inTx(d, func(q db.Q) error { return tell.SessionConfirmed(q, agent, op) }); err != nil && log != nil {
				log("  %s: telling the delivery of %s failed: %v", agent, op, err)
			}
		},
		OnFailed: func(agent, op, why string, written bool) {
			if log != nil {
				log("  %s: %s not delivered: %s", agent, op, why)
			}
			if err := tell.SessionNotDelivered(agent, op, why, written); err != nil && log != nil {
				log("  %s: telling that %s was not delivered failed: %v", agent, op, err)
			}
		},
	}
}

// inTx runs fn in a transaction: the ledger move, the event and the sender's note of one outcome are one write.
func inTx(d *sql.DB, fn func(q db.Q) error) error {
	tx, err := d.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := fn(tx); err != nil {
		return err
	}
	return tx.Commit()
}

func findSession(home string, d *sql.DB, agent string) (peerinbox.Session, error) {
	if d == nil { // the daemon's database is not open yet
		return peerinbox.Session{}, peerinbox.ErrNoSession
	}
	return agentSession(home, d, agent)
}

// agentSession is the live session of an agent, found through its row's account and worktree.
func agentSession(home string, q db.Q, agent string) (peerinbox.Session, error) {
	st, err := db.GetAgentState(q, agent)
	if err != nil {
		return peerinbox.Session{}, err
	}
	cwd := ""
	if st != nil {
		cwd = lifecycle.WorktreePath(st.Worktree.String) // the column may hold `<repo>::<path>`
	}
	if cwd == "" || st.Account.String == "" {
		return peerinbox.Session{}, peerinbox.ErrNoSession
	}
	config, err := team.AccountConfigDir(home, st.Account.String)
	if err != nil {
		return peerinbox.Session{}, errors.New("no config directory for the agent's account")
	}
	return peerinbox.ByCwd(config, cwd, func(pid int) bool { return syscall.Kill(pid, 0) == nil })
}

// mixedAgents is supervisor.Agents for a daemon with structured agents and peer-inbox agents: each call goes to the one the agent is
// delivered through.
type mixedAgents struct {
	structured supervisor.Agents
	peer       supervisor.Agents
	isPeer     func(agent string) bool
}

var _ supervisor.Agents = mixedAgents{}

func (m mixedAgents) of(agent string) supervisor.Agents {
	if m.isPeer(agent) {
		return m.peer
	}
	return m.structured
}

func (m mixedAgents) Structured(agent string) bool { return m.of(agent).Structured(agent) }
func (m mixedAgents) Live(agent string) bool       { return m.of(agent).Live(agent) }
func (m mixedAgents) Send(agent, op, text string, d structured.Delivery) (structured.Receipt, error) {
	return m.of(agent).Send(agent, op, text, d)
}
func (m mixedAgents) Withdraw(agent, op string) (structured.Receipt, bool, error) {
	return m.of(agent).Withdraw(agent, op)
}
func (m mixedAgents) Promote(agent, op string) (structured.Receipt, error) {
	return m.of(agent).Promote(agent, op)
}

// classOf is the permission class an agent's session runs in: bypass when its prompts are skipped, else prompting.
func classOf(roster *team.Roster, agent string) string {
	cfg, err := roster.Read(agent)
	if err != nil {
		return ""
	}
	if roster.ModeOf(agent, cfg) == "bypass" {
		return peerinbox.Bypass
	}
	return peerinbox.Prompting
}

// peerFrom is who the message op to a session is from, and the class attested for it. The name is whoever wrote it (the agents named in
// the operation, "founder" for the person), else the daemon: a message always says it is another's, never reads as the person's own typing.
//
// The class: the daemon is not a session with a mode of its own, it speaks for whoever wrote the message. The person, and the daemon's own
// words, have the authority the agent was launched with, so they attest the agent's own class; an agent that wrote it attests its own, and
// the weakest of several authors counts, so a prompting agent's words are held by a session that bypasses prompts and never pass as the
// person's. Only a session that bypasses prompts is attested to; any other takes the message as it did without. An operation whose text is
// not known to be the person's, the daemon's or an agent's attests nothing.
func peerFrom(home string, d *sql.DB) func(agent, op string) peerinbox.From {
	roster := team.NewRoster(home)
	return func(agent, op string) peerinbox.From {
		authors, known := supervisor.AuthorsOfOp(d, op)
		var from peerinbox.From
		if known {
			var names []string
			for _, a := range authors {
				if a == db.Human {
					a = "founder"
				}
				if a != agent && !slices.Contains(names, a) {
					names = append(names, a)
				}
			}
			from.Name = strings.Join(names, ", ")
		}
		if !known || classOf(roster, agent) != peerinbox.Bypass {
			return from
		}
		from.Mode = peerinbox.Bypass
		for _, author := range authors {
			if !slices.Contains(paths.ListAgents(home), author) || author == agent {
				continue
			}
			if c := classOf(roster, author); c != peerinbox.Bypass {
				from.Mode = c
				break
			}
		}
		return from
	}
}
