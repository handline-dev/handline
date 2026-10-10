package compose

import (
	"context"
	"database/sql"
	"errors"
	"syscall"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/peerport"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
)

// PeerKey is the top-level agent.yaml key (`peer_inbox: true`) that has an agent's messages delivered through its terminal session's peer
// inbox. Off for every agent until its own agent.yaml says so.
const PeerKey = "peer_inbox"

// PeerFromYAML reads PeerKey from an agent's agent.yaml.
func PeerFromYAML(home string) func(agent string) bool { return yamlFlag(home, PeerKey) }

// peerDelivery is the Telling the peer port reports to.
type peerDelivery interface {
	SessionConfirmed(q db.Q, agent, op string) error
	SessionNotDelivered(q db.Q, agent, op, why string) error
}

// NewPeerPort is the Agents port of the terminal agents that peer reports through: the session is the live one in the agent's worktree under
// its account, a message is delivered once its transcript shows it, and the outcome goes to tell.
func NewPeerPort(home string, d *sql.DB, peer func(agent string) bool, tell peerDelivery, log func(string, ...any)) *peerport.Port {
	return &peerport.Port{
		Peer:   peer,
		Find:   func(agent string) (peerinbox.Session, error) { return findSession(home, d, agent) },
		Sender: peerinbox.New(),
		Ctx:    context.Background(),
		OnDelivered: func(agent, op string) {
			if err := tell.SessionConfirmed(d, agent, op); err != nil && log != nil {
				log("  %s: telling the delivery of %s failed: %v", agent, op, err)
			}
		},
		OnFailed: func(agent, op, why string) {
			if log != nil {
				log("  %s: %s not delivered: %s", agent, op, why)
			}
			if err := tell.SessionNotDelivered(d, agent, op, why); err != nil && log != nil {
				log("  %s: telling that %s was not delivered failed: %v", agent, op, err)
			}
		},
	}
}

func findSession(home string, d *sql.DB, agent string) (peerinbox.Session, error) {
	if d == nil { // the daemon's database is not open yet
		return peerinbox.Session{}, peerinbox.ErrNoSession
	}
	st, err := db.GetAgentState(d, agent)
	if err != nil {
		return peerinbox.Session{}, err
	}
	if st == nil || !st.Worktree.Valid || st.Account.String == "" {
		return peerinbox.Session{}, peerinbox.ErrNoSession
	}
	config, err := team.AccountConfigDir(home, st.Account.String)
	if err != nil {
		return peerinbox.Session{}, errors.New("no config directory for the agent's account")
	}
	return peerinbox.ByCwd(config, st.Worktree.String, func(pid int) bool { return syscall.Kill(pid, 0) == nil })
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
