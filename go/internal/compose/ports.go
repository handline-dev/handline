package compose

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
)

// SupervisorPorts are the supervisor's ports for the terminal agents: the lifecycle, the transcript, the handoff and the terminal (its launch, its screen reads) are real; typing at a terminal session and the dialogs are not served. The caller
// sets them on the Env, then calls SettlePeerSends.
func (p *Parts) SupervisorPorts(lc *Lifecycle) supervisor.Ports {
	set := lc.Manager.Settings
	return supervisor.Ports{
		Agents:    p.agentsPort(),
		Terminal:  termPort{lc.term},
		Observer:  liveSessions{p: p},
		Dialogs:   noDialogs{},
		Waits:     waits{},
		Idle:      idle{set},
		Sessions:  sessions{p: p, ho: lc.Handoff},
		Lifecycle: lifecycleStarter{m: lc.Manager},
		Evaluate:  evaluate{},
		Proposals: team.TrustProposals{Trust: team.NewTrust(p.home), Q: p.db},
		Stuck:     stuck{},
	}
}

type liveSessions struct{ p *Parts }

func (o liveSessions) LiveSessions() (map[string]bool, bool) {
	out := map[string]bool{}
	for agent := range o.p.terminalSessions() {
		out[agent] = true
	}
	return out, true
}

type noDialogs struct{}

func (noDialogs) PastDialog(db.Q, string, bool) (string, string) { return "", "" }
func (noDialogs) Reason(kind, detail string) string              { return session.Reason(kind, detail) }
func (noDialogs) DecisionAfterS() int64                          { return session.DecisionAfterS }

// waits: no agent's permission prompt is held by this daemon, so none waits on one here.
type waits struct{}

func (waits) WaitingOnPrompt(db.Q, *db.AgentState) bool { return false }

func (waits) AtLimit(q db.Q, agent string) bool {
	at, err := handoff.AtLimit(q, agent)
	return err == nil && at != nil
}

type idle struct{ s *lifecycle.Settings }

func (i idle) NoticeDigestS(agent string) int64           { return i.s.NoticeDigestS(agent) }
func (i idle) CloseTimeoutS(agent string) int64           { return i.s.CloseTimeoutS(agent) }
func (i idle) Never() int64                               { return i.s.Never() }
func (i idle) BgGraceS(agent string) int64                { return i.s.BgGraceS(agent) }
func (i idle) ConfigSeconds(name string, def int64) int64 { return i.s.ConfigSeconds(name, def) }
func (idle) FooterCount(running string) int               { return session.FooterCount(running) }

// sessions: an agent's live session is the one stored with it; there is no context meter to read.
type sessions struct {
	p  *Parts
	ho *handoff.Env
}

func (s sessions) LiveSession(q db.Q, agent string) string {
	row, err := db.GetAgentState(q, agent)
	if err != nil || row == nil {
		return ""
	}
	return row.ResumeSession.String
}

func (sessions) ContextUse(db.Q, string) (int64, int64) { return -1, 0 }

// KeepsTranscripts: the Claude CLI leaves a transcript under the account's config dir.
func (s sessions) KeepsTranscripts(account string) bool {
	return team.AccountVendor(s.p.home, account) == team.DefaultVendor
}

func (s sessions) FindTranscript(session, account string) string {
	return s.ho.FindTranscript(session, account)
}

func (s sessions) CacheLeft(agent string, now int64) (int64, bool) {
	clock, err := s.ho.CacheClock(agent, "")
	if err != nil || clock == nil || clock.TTL == 0 {
		return 0, false
	}
	left, ok := clock.Left(float64(now))
	return int64(left), ok
}

// MovableTrigger is daemon/handoff.MOVABLE_TRIGGERS.
func (sessions) MovableTrigger(kind string) bool {
	switch kind {
	case "usage", "usage_7d", "forced", "login":
		return true
	}
	return false
}

type lifecycleStarter struct{ m *lifecycle.Manager }

func (l lifecycleStarter) StartForWork(_ db.Q, agent string) string {
	said, err := l.m.StartForWork(agent)
	if err != nil {
		return fmt.Sprintf("%s: could not start for work: %v", agent, err)
	}
	return said
}

func (l lifecycleStarter) WakeAgent(_ db.Q, agent string) (bool, string) {
	ok, said, err := l.m.WakeAgent(agent, lifecycle.WakeOpts{})
	if err != nil {
		return false, err.Error()
	}
	return ok, said
}

func (l lifecycleStarter) AccountWait(q db.Q, agent string) (string, error) {
	row, err := db.GetAgentState(q, agent)
	if err != nil {
		return "", err
	}
	switch w := l.m.Launch.WakeWait(q, agent, row); {
	case w == nil:
		return "", nil
	case w.Slot:
		return "cap", nil
	}
	return "wall", nil
}

// evaluate is daemon/evaluate.queued_or_idle: QUEUED when something has not reached the agent yet, IDLE otherwise.
type evaluate struct{}

func (evaluate) QueuedOrIdle(q db.Q, agent, when string) error {
	n, err := db.UnreachedCount(q, agent)
	if err != nil {
		return err
	}
	if n > 0 {
		return db.SetAgentState(q, agent, states.Queued, 0, when)
	}
	return db.SetAgentState(q, agent, states.Idle, 0, when+"; no requests waiting")
}

// stuck: no delivery is blocked by what a screen shows.
type stuck struct{}

func (stuck) DeliveryBlock(db.Q, string) (string, bool) { return "", false }

// agentsPort is the supervisor's Agents: the peer inbox for the terminal agents whose agent.yaml does not turn peer_inbox off, and no
// session port for the others. The peer's outcomes go to whatever Bind gave as the telling.
func (p *Parts) agentsPort() supervisor.Agents {
	peer := NewPeerPort(p.home, p.db, p.Peer, p.panePid, peerTell{p}, p.log)
	return mixedAgents{none: noSessionPort{}, peer: peer, isPeer: p.Peer}
}

// panePid is the pid of the process the terminal runs; 0 before the terminal host is up or when it cannot say.
func (p *Parts) panePid(handle string) int {
	if p.LC == nil || p.LC.term == nil {
		return 0
	}
	return p.LC.term.PanePid(handle)
}

// peerTell hands the peer port's outcomes to the telling that Bind set, when there is one.
type peerTell struct{ p *Parts }

func (t peerTell) SessionConfirmed(q db.Q, agent, op string) error {
	if d, ok := t.p.tell.(peerDelivery); ok {
		return d.SessionConfirmed(q, agent, op)
	}
	return nil
}

func (t peerTell) SessionNotDelivered(agent, op, why string, written bool) error {
	if d, ok := t.p.tell.(peerDelivery); ok {
		return d.SessionNotDelivered(agent, op, why, written)
	}
	return nil
}

func (t peerTell) SessionLost(q db.Q, agent string) error {
	if d, ok := t.p.tell.(peerDelivery); ok {
		return d.SessionLost(q, agent)
	}
	return nil
}
