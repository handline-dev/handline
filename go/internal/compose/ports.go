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

// SupervisorPorts are the supervisor's ports for a daemon that runs structured agents and terminal ones: the lifecycle, the transcript, the
// handoff and the terminal (its launch, its screen reads) are real; typing at a terminal session and the dialogs are not served. The caller
// sets them on the Env.
func (p *Parts) SupervisorPorts(lc *Lifecycle) supervisor.Ports {
	set := lc.Manager.Settings
	return supervisor.Ports{
		Agents:    p.Agents,
		Terminal:  termPort{lc.term},
		Observer:  liveSessions{p: p},
		Dialogs:   noDialogs{},
		Waits:     waits{p: p},
		Idle:      idle{set},
		Sessions:  sessions{p: p, ho: lc.Handoff},
		Lifecycle: lifecycleStarter{m: lc.Manager},
		Evaluate:  evaluate{},
		Channel:   noChannel{},
		Proposals: proposals{trust: team.NewTrust(p.home), q: p.db},
		Stuck:     stuck{},
	}
}

type liveSessions struct{ p *Parts }

func (o liveSessions) LiveSessions() (map[string]bool, bool) {
	out := map[string]bool{}
	for agent := range o.p.terminalSessions() {
		out[agent] = true
	}
	for agent := range o.p.Manager.Live() {
		out[agent] = true
	}
	return out, true
}

type noDialogs struct{}

func (noDialogs) PastDialog(db.Q, string, bool) (string, string) { return "", "" }
func (noDialogs) Reason(kind, detail string) string              { return session.Reason(kind, detail) }
func (noDialogs) DecisionAfterS() int64                          { return session.DecisionAfterS }

// waits: a structured agent is waiting on a prompt while its permission prompt is open.
type waits struct{ p *Parts }

func (w waits) WaitingOnPrompt(_ db.Q, row *db.AgentState) bool {
	return row != nil && w.p.Manager.Broker != nil && w.p.Manager.Broker.Has(row.Agent)
}

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

// sessions: the session of a structured agent is the one stored with it; it has no context meter to read.
type sessions struct {
	p  *Parts
	ho *handoff.Env
}

func (s sessions) LiveSession(q db.Q, agent string) string {
	if id := s.p.Manager.SessionID(agent); id != "" {
		return id
	}
	row, err := db.GetAgentState(q, agent)
	if err != nil || row == nil {
		return ""
	}
	return row.ResumeSession.String
}

func (sessions) ContextUse(db.Q, string) (int64, int64) { return -1, 0 }

// KeepsTranscripts: the Claude CLI leaves a transcript under the account's config dir, structured or not.
func (s sessions) KeepsTranscripts(account string) bool {
	return team.AccountVendor(s.p.home, account) == team.DefaultVendor
}

func (s sessions) FindTranscript(session, account string) string {
	return s.ho.FindTranscript(session, account)
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

// noChannel: there are no watchers of a terminal to tell, and nobody else holds an input that does not exist.
type noChannel struct{}

func (noChannel) Marker(db.Q, string, string, map[string]any)                       {}
func (noChannel) RecordScreenSample(db.Q, string, *db.AgentState, string, []string) {}
func (noChannel) HeldByOther(db.Q, string) string                                   { return "" }
func (noChannel) HeldWhy(string, string) string                                     { return "" }

// proposals: the open trust proposal a founder's card waits on is found from the proposal files; the founder's answer is applied by the
// Python daemon's trust apply until that is ported, and the proposer is told so.
type proposals struct {
	trust *team.Trust
	q     db.Q
}

func (p proposals) OpenProposalOf(rid int64) string {
	list, err := p.trust.Listing()
	if err != nil {
		return ""
	}
	var decisions []int64 // the decision tasks whose card is rid, in whatever state
	if tasks, err := db.TasksForRequest(p.q, rid); err == nil {
		for _, t := range tasks {
			if t.Expects.String == "decision" {
				if first, err := db.TaskRequests(p.q, t.ID); err == nil && len(first) > 0 && first[0].ID == rid {
					decisions = append(decisions, t.ID)
				}
			}
		}
	}
	for _, prop := range list {
		if prop.Get("state") != "proposed" {
			continue
		}
		if task, ok := asInt(prop.Get("task")); ok && task != 0 {
			for _, d := range decisions {
				if d == task {
					return fmt.Sprint(prop.Get("id"))
				}
			}
			continue
		}
		if n, ok := asInt(prop.Get("request")); ok && n == rid {
			return fmt.Sprint(prop.Get("id"))
		}
	}
	return ""
}

func (proposals) Answered(_ db.Q, pid string, _ *db.Request, _ string) string {
	return fmt.Sprintf("proposal %s was answered; applying a trust proposal is not served by this daemon yet", pid)
}

func asInt(v any) (int64, bool) {
	switch n := v.(type) {
	case int:
		return int64(n), true
	case int64:
		return n, true
	}
	return 0, false
}

// stuck: a structured agent's delivery is blocked by nothing the screen shows.
type stuck struct{}

func (stuck) DeliveryBlock(db.Q, string) (string, bool) { return "", false }
