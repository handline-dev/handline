package session

import (
	"database/sql"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/term"
)

// Terminal is session/term.py as this package uses it. *term.Term satisfies it.
type Terminal interface {
	Available() bool
	Alive(handle string) bool
	LiveTerminals() map[string]bool
	ScreenBlocker(handle string) *term.Blocker
	ScreenBlockerCached(handle string, notBefore time.Time) *term.Blocker
	UsageLimitOpen(handle string) bool
	DeclineDialog(handle string) (outcome, title, why string)
	StopWorkAndExit(handle string) (outcome, title, why string)
	DialogOnScreen(handle string) *term.Dialog
	ScreenText(handle string) (string, bool)
	AcceptsTool(handle, tool string) bool
	AcceptDialog(handle, tool string) (outcome, title, why string)
	AnswerDialog(handle string, choose func(vendor string, d *term.Dialog) int) (outcome, title, why string)
	VendorOf(handle string) string
}

// Delivery is the part of daemon/delivery.py (and the db reads beside it) that blocked.py asks: what the daemon wants to type at an
// agent and has not, and whether a session is there to type at. G5 implements it.
type Delivery interface {
	// EffectiveHead is the request at the head of the agent's queue that is still untold, nil for none.
	EffectiveHead(agent string) *Request
	// SessionChanged is whether the agent is in a session the daemon has not yet told its queue to.
	SessionChanged(agent string) bool
	// QueueDepth is how many requests are queued at the agent.
	QueueDepth(agent string) int
	// UnannouncedAnswers is the ids of what the agent asked that came back and it has not been told, oldest first.
	UnannouncedAnswers(agent string) []int64
	// ForgottenResolve is the request the agent has sat idle on without resolving, nil when it has not.
	ForgottenResolve(agent string, row *AgentRow, now int64) *Forgotten
	// NudgeAfterS is how long an idle agent is left before it is reminded to resolve.
	NudgeAfterS() int64
	HeadTaskBlocked(agent string, head *Request) bool
	// WallReason is why the agent's usage wall stands, "" when none.
	WallReason(agent string, row *AgentRow) string
	// SessionPresent is whether the process table lists a session for the agent.
	SessionPresent(agent string) bool
	// TableListsSession is whether the process table lists one; nil when the table could not be read.
	TableListsSession(agent string) *bool
	// SessionStillDrawn is whether the terminal's screen still draws a session.
	SessionStillDrawn(handle string, wait bool) bool
}

// Request is a queued request as blocked.py reads it: its id.
type Request struct{ ID int64 }

// Forgotten is a request the agent has sat idle on: how long, and the head it sits on.
type Forgotten struct {
	IdleS int64
	Head  *Request
}

// Asker is team/ask.py and the request table as blocked.py reaches them.
type Asker interface {
	// Report puts a note in front of the founder, about agent.
	Report(agent, text, subject string)
	// RaiseDecision raises a request to the founder and returns its id.
	RaiseDecision(agent, text, subject string) int64
	// ResolveDone closes request rid as done, without notifying.
	ResolveDone(rid int64, text string)
	// RequestState is the state of request rid, "" when there is none.
	RequestState(rid int64) string
	// OpenDecisionsOf is how many decisions about the agent are still waiting on the founder.
	OpenDecisionsOf(agent string) int
}

// Lifecycle is daemon/lifecycle.py's two standing consents, for a dialog the daemon may accept on its own.
type Lifecycle interface {
	AutoTrust(agent string) bool
	AutoBypass(agent string) bool
}

// Config is config.yaml's global settings.
type Config interface {
	Flag(key string, def bool) bool
	SetFlag(key string, value bool) error
}

// Rules is the founder's standing answers to dialogs (team/dialog_rules).
type Rules interface {
	// Titles is every (vendor, title) some rule covers.
	Titles() map[[2]string]bool
	// Match is the number of the option a rule picks on this dialog, 0 for none.
	Match(vendor, title string, numbers []int, labels []string) int
}

// Held is daemon/inputhold.py: who else holds an agent's terminal input.
type Held interface {
	HeldByOther(agent string) string
}

// Env is what the session code works on. Every field but DB, Home and Term may be nil where its code is not reached.
type Env struct {
	DB        *sql.DB
	Home      string
	Term      Terminal
	Delivery  Delivery
	Asker     Asker
	Lifecycle Lifecycle
	Config    Config
	Rules     Rules
	Held      Held
	// Agents is the registered agents (paths.ListAgents).
	Agents func() []string
	Now    func() time.Time
	Log    func(format string, a ...any)

	mu      sync.Mutex
	checked map[string]int64
	probed  map[string]int64
}

func (e *Env) now() int64 {
	if e.Now != nil {
		return e.Now().Unix()
	}
	return time.Now().Unix()
}

func (e *Env) log(format string, a ...any) {
	if e.Log != nil {
		e.Log(format, a...)
	}
}
