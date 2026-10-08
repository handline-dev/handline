package lifecycle

import (
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/term"
)

// Waits the lifecycle runs on (infra/waits.py and delivery's own).
const (
	WakeSessionTimeoutS = 15
	// MaxStartupScreens: the folder-trust and bypass screens, in either order, with room for a dialog or two.
	MaxStartupScreens = 4
)

// TableRetryPauses are the pauses between reads of a process table that missed: a session just ended leaves the table while it is
// read, which is the moment a restart reads it.
var TableRetryPauses = []time.Duration{300 * time.Millisecond, 700 * time.Millisecond}

// WakeBackoffS is the pause after the first, second and later failed wakes of a restart owed.
var WakeBackoffS = []int64{10, 30, 90}

// Delivery is daemon/delivery.py as the lifecycle uses it; *supervisor.Env satisfies it.
type Delivery interface {
	BgHolds(q db.Q, agent string, now int64) (bool, error)
	IdleSince(q db.Q, agent string) (int64, bool, error)
	BgOwesNothing(q db.Q, agent string) (bool, error)
	BgMayStop(q db.Q, agent string, now int64) (bool, error)
	BgPromptedAt(q db.Q, agent string) (int64, error)
	BgSilentS(q db.Q, agent string, now int64) (int64, bool, error)
	HeadTaskBlocked(q db.Q, agent string, head *db.Request) (bool, error)
	EffectiveHead(q db.Q, agent string) (*db.Request, error)
	ForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) (*supervisor.Forgotten, error)
	SessionChanged(q db.Q, agent string) (bool, error)
	TypeGate(q db.Q, agent, handle string, requireIdle, requireSettled bool) (string, error)
	MarkTyped(q db.Q, agent string, by map[string]any) error
	SessionStillDrawn(handle string, wait bool) bool
	WaitSessionGone(agent string) bool
	WaitForSession(agent string, timeoutS int, promptFile string) bool
	WaitingPrompt(q db.Q, agent string) (text string, told func(session string) error, ok bool, err error)
	EnforceEdges(q db.Q, agent string) (bool, error)
	SessionPresent(agent string) (bool, error)
	PastDialog(q db.Q, agent string) (string, error) // delivery._past_dialog
}

// HandoffPort is session/handoff.py, daemon/handoff.py and handoff_note.py as the lifecycle uses them.
type HandoffPort interface {
	// Capture reads the handoff record of a live session; missing names what could not be read.
	Capture(q db.Q, agent string) (text string, missing []string, err error)
	// Record takes and keeps the record (db.set_handoff) for a session; the gaps it could not read. closedSession names the session of a
	// closed agent, "" for the live one.
	Record(q db.Q, agent, closedSession string) (gaps []string, err error)
	MemoryLine(agent string) string
	// PreviousSession is the agent's previous session and its transcript's path ("" when none).
	PreviousSession(q db.Q, agent, account string) (session, path string)
	RequestLine(q db.Q, agent string) string
	// MovePlan is the account a pinned agent can start fresh on, and why not when there is none; BackingOff says a move was tried too lately.
	MovePlan(q db.Q, agent, kind string) (account, why string)
	BackingOff(q db.Q, agent string) bool
	// AnsweredTurn: the agent has answered the ask for its handoff note, restarting its idle clock.
	AnsweredTurn(q db.Q, agent string) bool
	// CacheClock is when the session last answered and how long its prompt cache lives; ok false when the transcript gives no clock.
	CacheClock(q db.Q, agent, session string) (at, ttl int64, ttlKnown, ok bool)
	CacheMargin(ttl int64) int64
	ClearNote(q db.Q, agent string)
}

// Terminal is session/term.py as the lifecycle uses it. *term.Term satisfies the part that has landed; the closing, relaunching and
// bypass-warning calls are G4's.
type Terminal interface {
	Available() bool
	WaitIdle(handle string) bool
	TypeableNow(handle string) bool
	ScreenBlocker(handle string) *term.Blocker
	BackgroundWork(handle string) (running string, known bool)
	TrustPromptShowing(handle string) bool
	TrustPromptFolder(handle string) string
	ConfirmTrust(handle string) (bool, string)
	CloseSession(handle string) (bool, string)  // types /exit
	CloseTerminal(handle string) (bool, string) // closes the pane
	Relaunch(handle, cmd string, started func() bool) (bool, string)
	ResumeWith(handle, pointer string) (bool, string)
	BypassPromptShowing(handle string) bool
	AcceptBypass(handle string) (bool, string)
	WaitForTrustPrompt(handle string, showing bool)
	// DriverScope holds one driver lookup for the calls made inside it; the returned func ends it.
	DriverScope() func()
}

// Blocked is session/blocked.py as the lifecycle uses it.
type Blocked interface {
	NoteCloseFailure(q db.Q, agent string)
	// PastDialog gets past a dialog on screen; ok is whether it went.
	PastDialog(q db.Q, agent string) (ok bool, why string)
	StopWorkAndExit(q db.Q, agent string) bool
	// AnswerByRule presses a standing rule's option for the dialog on screen.
	AnswerByRule(q db.Q, agent, handle string) bool
	AutoAcceptEnabled() bool
}

// Launch is session/launch.py (the next group of this port): what a wake launches and where it can.
type Launch interface {
	Drifted(agent string, row *db.AgentState) bool
	// WakeWait is why the account a wake would land on is full, nil when it has room.
	WakeWait(q db.Q, agent string, row *db.AgentState) *WakeWait
	DescribeWakeWait(w *WakeWait) string
	// CommandFor is the launch command for a wake; account "" for the agent's own.
	CommandFor(agent, account, why, by string) (string, error)
	ChooseAccount(q db.Q, agent string) bool
	NoRoom(agent string) string
}

// Spawner is team/spawn.py.
type Spawner interface {
	// WorktreePath is the worktree if it still exists, "" otherwise.
	WorktreePath(worktree string) string
	StartIn(agent, worktree, cmd string) (bool, string)
	Spawn(agent, repo string) (bool, string)
}

// AgentsPort is team/agents.py, trust.py and measure as the lifecycle reads them.
type AgentsPort interface {
	RepoOf(agent string) string
	// ReadConfig fails for an agent.yaml that will not read.
	ReadConfig(agent string) error
	CurrentAccount(q db.Q, agent string) string
	SkipPermissionsInEffect(agent string) (bool, error)
	IsDeclared(folder string) bool
	MeasureBegin(q db.Q, agent, account string)
	RaiseDecision(q db.Q, agent, text, reason string) error
	VendorName(agent string) string
}

// SessionStatus is usage/status.py's reading of an agent's session.
type SessionStatus struct {
	SessionID string
	CtxPct    any // nil when unknown
}

// StatusPort reads it.
type StatusPort interface {
	ReadForAgent(agent, account string) SessionStatus
}

// Observer is session/observe.live_sessions: the agents with a running session and its pid; ok false when the table could not be read.
type Observer interface {
	LiveSessions() (map[string]int, bool)
}

// Typing is the terminal lock and the hold another controller takes on an agent's input.
type Typing interface {
	// TakeTerminal is the right to type at agent, nil-release false when someone else holds it.
	TakeTerminal(agent string) (release func(), ok bool)
	HeldByOther(q db.Q, agent string) string
	HeldWhy(agent, holder string) string
}

// Manager is daemon/lifecycle.py: closing and waking an agent's session, and the folder-trust prompt a wake can meet.
type Manager struct {
	Q        db.Q
	Home     string
	Log      func(format string, args ...any)
	Settings *Settings

	Delivery Delivery
	Handoff  HandoffPort
	Term     Terminal
	Blocked  Blocked
	Launch   Launch
	Spawn    Spawner
	Agents   AgentsPort
	Status   StatusPort
	Observe  Observer
	Typing   Typing

	Now   func() int64
	Sleep func(time.Duration)
}

func (m *Manager) now() int64 {
	if m.Now != nil {
		return m.Now()
	}
	return time.Now().Unix()
}

func (m *Manager) sleep(d time.Duration) {
	if m.Sleep != nil {
		m.Sleep(d)
		return
	}
	time.Sleep(d)
}

var _ Delivery = (*supervisor.Env)(nil)
