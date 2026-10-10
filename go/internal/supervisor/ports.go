// Package supervisor is the Python daemon's telling layer: ask routing, sends, messaging, landing, delivery, the task queue,
// holds and the notice digest (team/ask.py, daemon/{sends,messaging,landing,delivery,taskqueue,taskholds,noticedigest}.py).
//
// It reaches everything it does not own through the ports below, one small interface each, so the groups that own them (G2
// usage, G3b observe/blocked/waiting, G4 terminal channel, G6 lifecycle, G7 handoff, G8 evaluate) only have to satisfy them.
// Tests use fakes.
package supervisor

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/term"
)

// Terminal is session/term.py as the telling layer uses it: the terminal host, with the screen reads and typing on top.
// *term.Term satisfies the part Go has (SessionOnScreen, ScreenBlocker, BackgroundWork, Send); the rest is G3b's wrapper.
type Terminal interface {
	Available() bool
	Missing() string // why it is not (term.missing)
	Alive(handle string) bool
	TypeableNow(handle string) bool
	ScreenBlocker(handle string) *term.Blocker
	SessionOnScreen(handle string) (session, known bool)
	ComposerOnScreen(handle string) (box string, known bool)
	IsTextTyped(box, text string) bool
	BackgroundWork(handle string) (running string, known bool)
	LastTail(handle string) []string
	Send(handle, text string, enter bool) (bool, string)
	SendToSession(handle, text string) (bool, string) // refuses to type at a shell prompt (term.send_to_session)
	SendCtrlC(handle string)                          // Ctrl-C without Enter, then the shell settle wait
	ClearContext(handle string) (bool, string)
	ClearCommand(handle string) string
}

// Observer is session/observe.py: what the process table says.
type Observer interface {
	// LiveSessions is the agents with a running session; ok is false when the table could not be read.
	LiveSessions() (sessions map[string]bool, ok bool)
}

// Dialogs is session/blocked.py: getting past a dialog, and the words for a recorded block.
type Dialogs interface {
	// PastDialog gets past a dialog standing before a type at the agent: "" when nothing stands in the way, else why and the dialog's title.
	PastDialog(q db.Q, agent string, onlyLimit bool) (why, title string)
	Reason(kind, detail string) string
	DecisionAfterS() int64 // how long a dialog stands before the founder is told
}

// Waits is session/waiting.py and session/events.py: whether the agent's screen is on a permission prompt, and whether its last turn ended on the limit.
type Waits interface {
	WaitingOnPrompt(q db.Q, row *db.AgentState) bool
	AtLimit(q db.Q, agent string) bool
}

// Idle is session/idle_close.py and bgjobs.py: the timeouts config.yaml and the agent's own settings give.
type Idle interface {
	NoticeDigestS(agent string) int64
	CloseTimeoutS(agent string) int64
	Never() int64 // CloseTimeoutS's "never"
	BgGraceS(agent string) int64
	ConfigSeconds(name string, def int64) int64
	FooterCount(running string) int // the background jobs a footer reports
}

// Sessions is usage/status.py and session/handoff.py as delivery reads them: which session an agent is in, and where its transcript is.
type Sessions interface {
	// LiveSession is the id of the session the agent is in now, "" when unknown.
	LiveSession(q db.Q, agent string) string
	// ContextUse is the context the session has used and its limit, for the clear notice; used < 0 when unknown.
	ContextUse(q db.Q, agent string) (used, limit int64)
	// KeepsTranscripts says whether the account's vendor leaves a transcript to read.
	KeepsTranscripts(account string) bool
	// FindTranscript is the transcript file of a session under an account, "" when none.
	FindTranscript(session, account string) string
	// MovableTrigger says whether a handoff trigger parks the agent until its usage window rolls over.
	MovableTrigger(kind string) bool
}

// Lifecycle is daemon/lifecycle.py (G6): starting and waking an agent's session.
type Lifecycle interface {
	// StartForWork starts an agent that has no terminal because work is waiting; the words for the log.
	StartForWork(q db.Q, agent string) string
	WakeAgent(q db.Q, agent string) (ok bool, why string)
}

// Evaluate is daemon/evaluate.py (G8): the state an agent returns to once nothing it asked is outstanding.
type Evaluate interface {
	QueuedOrIdle(q db.Q, agent, why string) error
}

// Channel is daemon/termchannel.py, screensamples.py and inputhold.py (G4): what whoever watches a terminal is told.
type Channel interface {
	// Marker tells the terminal's watchers that the daemon (or a person's control call) typed.
	Marker(q db.Q, agent, kind string, by map[string]any)
	// RecordScreenSample keeps a sample of a screen that reported background work.
	RecordScreenSample(q db.Q, agent string, row *db.AgentState, running string, tail []string)
}

// Proposals is team/trust.py and decisions.py: the founder answering a trust proposal. Required: a reply or a resolve with no Proposals
// port closes nothing, since it cannot tell a proposal's card from any other.
type Proposals interface {
	// OpenProposalOf is the id of the open trust proposal a request is the card of, "" for none.
	OpenProposalOf(rid int64) string
	// Answered carries out the founder's answer to proposal pid; the line for the proposer.
	Answered(q db.Q, pid string, card *db.Request, verdict string) string
}

// Stuck is actions/stuck.py (_delivery_block): why work is not reaching an agent, in the daemon's own words.
type Stuck interface {
	// DeliveryBlock is (why, whether it resolves itself); "" when nothing is in the way.
	DeliveryBlock(q db.Q, agent string) (why string, clears bool)
}

// Agents is the agents' structured sessions (go/internal/structured), as the one send path uses them. A nil port means every agent is a
// terminal agent, and the typing code is what delivers.
type Agents interface {
	// Structured says whether agent runs as a structured session: its kind, not whether the process is up.
	Structured(agent string) bool
	// Live says whether the agent's session is open and answered initialize.
	Live(agent string) bool
	// Send delivers text under the operation id. The same op twice writes once and answers the first receipt. Now joins a running turn; Queue is
	// held until the turn ends. An error means nothing was written.
	Send(agent, op, text string, d structured.Delivery) (structured.Receipt, error)
	// Withdraw takes back a message still held behind a turn; ok is whether it was (also on a repeat). Promote writes a held message now.
	Withdraw(agent, op string) (structured.Receipt, bool, error)
	Promote(agent, op string) (structured.Receipt, error)
}

// Ports is every port; a nil field is an owner that has not landed yet.
type Ports struct {
	Agents    Agents // optional: nil is all-terminal
	Terminal  Terminal
	Observer  Observer
	Dialogs   Dialogs
	Waits     Waits
	Idle      Idle
	Sessions  Sessions
	Lifecycle Lifecycle
	Evaluate  Evaluate
	Channel   Channel
	Proposals Proposals
	Stuck     Stuck
}

// Wired says every port has its owner: only then can a write's telling run, since a missing port is a nil call. A daemon that serves the
// writes before the ports land makes them and leaves the telling to the tick.
func (p Ports) Wired() bool {
	return p.Terminal != nil && p.Observer != nil && p.Dialogs != nil && p.Waits != nil && p.Idle != nil && p.Sessions != nil &&
		p.Lifecycle != nil && p.Evaluate != nil && p.Channel != nil && p.Proposals != nil && p.Stuck != nil
}
