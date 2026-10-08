package tick

import "github.com/handline/handline/go/internal/db"

// Status is usage/status.py's reading of one agent's account and session, as the tick judges it. A nil figure is unknown, which is not a
// wall and not zero.
type Status struct {
	ModelID, SessionID, TranscriptPath string
	TrustedCtxUsed                     *int
	ShortTrusted, LongTrusted          *int
	// IsLiveReading: this payload reported the figure itself, not a carried one. Only a live reading may enter the burn history.
	IsLiveReading bool
	ShortAt       int64
	ShortPct      *int
}

// AccountReading is agents.account_reading: which account an agent's usage is landing on and how that is known ("recorded", "reported" or
// "assumed").
type AccountReading struct{ Account, Source string }

// Limit is the usage limit an agent's last turn ended on (events.at_limit).
type Limit struct{ Text, Source string }

// Need is lifecycle.start_or_wake_needed: "start" or "wake", and the sentence for what is waiting.
type Need struct{ Kind, What string }

// Host is what the tick asks of the machine it runs on: the agents and accounts it knows and the structured sessions it supervises.
type Host interface {
	// Agents is paths.list_agents, Accounts paths.list_accounts.
	Agents() []string
	Accounts() []string
	// SessionAlive is whether the agent has a live structured session; SessionIdle whether its S1 state is idle (not running, not
	// requires_action), so a restart will not cut a turn or an open permission prompt short.
	SessionAlive(agent string) bool
	SessionIdle(agent string) bool
	// RunningAccount is the account the agent's running process spends, "" when the sweep's snapshot has no process for it or its
	// environment cannot be read: "I don't know", which never contradicts anything.
	RunningAccount(agent string) string
}

// Usage is usage/status.py, usage/load.py and usage/measure.py as the tick reads them.
type Usage interface {
	AccountReading(q db.Q, agent string) (AccountReading, bool)
	ReadForAgent(agent, acct string) Status
	// ContextLimit is status.effective_context_limit for the agent's own config and the model read.
	ContextLimit(agent, modelID string) int
	WriteBurn(acct string, rate float64, at int64)
	// AtLimit is events.at_limit: the limit the agent's last turn ended on, nil when it did not.
	AtLimit(q db.Q, agent string) *Limit
	// Settle is measure.settle.
	Settle(q db.Q, agent string)
}

// Delivery is daemon/delivery.py as supervise calls it.
type Delivery interface {
	EnforceEdges(q db.Q, agent string) error
	NoteFailure(agent, why string) // why "" clears
	TrackBackgroundWork(q db.Q, agent string, row *db.AgentState)
	DeliverAnswers(q db.Q, agent string)
	DeliverQueued(q db.Q, agent string) bool
	NudgeForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64)
	// SessionPresent is delivery.session_present.
	SessionPresent(agent string) bool
	HasBackgroundWork(q db.Q, agent string) bool
	IdleSince(q db.Q, agent string) (int64, bool)
}

// Messaging is daemon/messaging.py and daemon/landing.py: what could not be typed at once is tried again every tick.
type Messaging interface {
	LandingSweep(q db.Q, agent string)
	TellUndelivered(q db.Q, agent string)
	DeliverPending(q db.Q, agent string)
}

// Lifecycle is daemon/lifecycle.py as the tick calls it.
type Lifecycle interface {
	WaitingFor(q db.Q, agent string) bool
	ResumableWithoutTerminal(row *db.AgentState) bool
	WakeAgent(q db.Q, agent string) (bool, string)
	StartOrWakeNeeded(q db.Q, agent string, row *db.AgentState) *Need
	StartForWork(q db.Q, agent string) string
	// ColdBeforeTyping is nil when it did not apply, true when it closed the session, false when it applied and closed nothing.
	ColdBeforeTyping(q db.Q, agent string, row *db.AgentState, now int64, st Status) *bool
	ShouldClose(q db.Q, agent, state string, row *db.AgentState, now int64, st Status) bool
	CloseAgent(q db.Q, agent string, st Status) (bool, string)
	CloseFresh(q db.Q, agent string, st Status) (bool, string)
	RestartAgent(q db.Q, agent string, st Status, by string, applying []string, cause string) (bool, string)
	TriggersApply(row *db.AgentState) bool
}

// Handoff is daemon/handoff.py and daemon/handoff_note.py.
type Handoff interface {
	SettleOwedClear(q db.Q, agent string) bool
	// Movable is whether the trigger kind may be answered by a move to another account (handoff.MOVABLE_TRIGGERS).
	Movable(kind string) bool
	// MovePlan is handoff._move_plan: the account to move to ("" for none) and why not.
	MovePlan(q db.Q, agent, kind string) (dest, why string)
	// MoveToRoom is handoff._move_to_room.
	MoveToRoom(q db.Q, agent string, t Trigger) bool
	// PerformHandoff is handoff.perform_handoff; dest "" lets it choose.
	PerformHandoff(q db.Q, agent string, t Trigger, dest string) bool
	// NoteHold is handoff_note.hold, NoteAtCacheEdge handoff_note.at_cache_edge.
	NoteHold(q db.Q, agent string, now int64) bool
	NoteAtCacheEdge(q db.Q, agent string, now int64, st Status)
	NoteEnabled(agent string) bool
	// NoteAsked is handoff_note.asked: the ask still waiting (ok) and how many turns have begun on it.
	NoteAsked(q db.Q, agent string) (turns int, ok bool)
	NoteAsk(q db.Q, agent, why string) bool
	NoteAnsweredTurn(q db.Q, agent string) bool
	NoteText(q db.Q, agent string) (string, bool)
	// AskMarginS is handoff_note.ASK_MARGIN_S.
	AskMarginS() int
}

// Reconciler is daemon/reconciler.py as the tick calls it.
type Reconciler interface {
	Sweep(q db.Q, actions *[]string)
	ExpireHandoffs(q db.Q) []string
	// StallAgent is reconciler._stall_agent: a real failure in one agent's turn stalls it, a resource shortage does not.
	StallAgent(q db.Q, agent string, actions *[]string, err error, kind string)
}

// Sweeps are the passes the tick makes before the per-agent turns and the ones it hands to the daemon's other packages.
type Sweeps struct {
	BlockedSweep    func(q db.Q, now int64)
	CollectSnapshot func(q db.Q) error
	PollUsage       func(q db.Q) error
	RelinkAccounts  func() (map[string][]string, error)
	// Rollover is fold.rollover for one agent: ok, and the message.
	Rollover func(agent string) (bool, string)
	// Today is fold.today.
	Today func() string
}

// Observe is what the tick's last pass feeds the burn estimate with (burnrate, tokenlog, usage samples).
type Observe interface {
	ActiveByAccount(q db.Q) map[string][]string
	FirstTurnAgents(q db.Q) map[string]bool
	ReadAccount(acct string) Status
	ObserveBurn(q db.Q, acct string, working []string, st Status, firsts map[string]bool) error
	RecordUsageSample(q db.Q, acct string, st Status) error
	PruneUsageSamples(q db.Q) error
	IngestTokens(q db.Q, acct string) error
	PruneTokens(q db.Q) error
	RefreshLongRatios(q db.Q) error
}

// Blocked is session/blocked.py's note for a stall.
type Blocked interface {
	DialogNote(q db.Q, agent string) string
}

// Config is the agent's own settings as the policies read them (idle_close._agent_cfg and the daemon's config.yaml).
type Config interface {
	// AgentConfig is the agent.yaml mapping, empty when unreadable.
	AgentConfig(agent string) map[string]any
	// Seconds is idle_close.config_seconds: the setting's value in seconds or the default.
	Seconds(key string, def int64) int64
}
