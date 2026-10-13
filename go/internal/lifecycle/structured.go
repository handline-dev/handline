package lifecycle

import (
	"crypto/rand"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// StartSpec is what a structured session is opened with. The runner turns it into the CLI's launch (account config dir, model, the
// agent's settings and environment); the lifecycle decides only which session and where.
type StartSpec struct {
	Agent, Worktree, Account string
	SessionID                string // a new id, or the stored one when Resume
	Resume                   bool
}

// AgentLocks serialises lifecycle acts on one structured agent in place of the terminal lock: a tick and a person asking for the same
// wake, or a spawn, must not open two processes. The zero value is ready; a nil *AgentLocks locks nothing.
type AgentLocks struct {
	mu    sync.Mutex
	locks map[string]*sync.Mutex
}

// Lock takes agent's lock; the returned func releases it.
func (a *AgentLocks) Lock(agent string) func() {
	if a == nil {
		return func() {}
	}
	a.mu.Lock()
	if a.locks == nil {
		a.locks = map[string]*sync.Mutex{}
	}
	l := a.locks[agent]
	if l == nil {
		l = &sync.Mutex{}
		a.locks[agent] = l
	}
	a.mu.Unlock()
	l.Lock()
	return l.Unlock
}

// Runner is the structured sessions the daemon owns, one per running agent (go/internal/structured through its manager). An agent is
// structured or a terminal agent; terminal agents keep the paths in the other files, frozen.
type Runner interface {
	Structured(agent string) bool
	// Open starts the CLI and returns once it has answered initialize; an early exit is an error.
	Open(spec StartSpec) error
	// Alive: any process of the agent's session group is running, not only the leader: a leader that died with children left is not gone.
	// Daemon-owned, so after a daemon restart nothing is alive until it is opened again.
	Alive(agent string) bool
	Busy(agent string) bool
	// Send writes text as a message to the running turn or a new one and returns once the CLI has echoed it; an error is not delivered.
	Send(agent, op, text string) error
	Interrupt(agent string) error
	// RetryingSince is when the CLI's current run of api_retry frames began (unix seconds), 0 when it is not retrying.
	RetryingSince(agent string) int64
	// Close ends the session; it errors while a process of the session is still alive.
	Close(agent string) error
	// WaitIdle reports whether the running turn ended within the timeout.
	WaitIdle(agent string, timeout time.Duration) bool
}

// Sessions opens structured sessions and keeps what a start leaves in the database. It stands where `hl launch` and the terminal host
// stand for a terminal agent.
type Sessions struct {
	Q      db.Q
	Run    Runner
	Launch Launch
	NewID  func() string
}

func (s *Sessions) newID() string {
	if s.NewID != nil {
		return s.NewID()
	}
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

// StartOpts are what a start records: why, who asked, and the first message.
type StartOpts struct {
	Cause, By string
	Resume    bool
	Session   string // the stored session id to resume
	Account   string // "" for the one this launch lands on
	Prompt    string // the first message; "" starts no turn
}

// Start opens agent's session in wt and records it. The returned id is the session now running.
func (s *Sessions) Start(agent, wt string, o StartOpts) (string, error) {
	if s.Run.Alive(agent) {
		return "", fmt.Errorf("%s already has a running session", agent)
	}
	id := o.Session
	if !o.Resume || id == "" {
		o.Resume, id = false, s.newID()
	}
	acct := o.Account
	if acct == "" {
		acct = s.Launch.AccountFor(s.Q, agent, o.Resume)
	}
	handoff, err := db.GetHandoff(s.Q, agent)
	if err != nil {
		return "", err
	}
	if err := s.Run.Open(StartSpec{Agent: agent, Worktree: wt, Account: acct, SessionID: id, Resume: o.Resume}); err != nil {
		return "", err
	}
	if o.Prompt != "" {
		// One op per start: the ledger outlives sessions, and a resume under the session id would be answered with the receipt of an
		// earlier start and never written.
		if err := s.Run.Send(agent, "start:"+s.newID(), o.Prompt); err != nil {
			// A session that cannot take its first message is not a started agent; ending it keeps the retry from meeting a live one. A
			// close that fails too is kept: the process is still there.
			return "", errors.Join(fmt.Errorf("started, but the first message was not delivered: %w", err), s.Run.Close(agent))
		}
	}
	if err := db.SetTerminal(s.Q, agent, "", wt); err != nil {
		return "", err
	}
	if err := db.SetResumeSession(s.Q, agent, id); err != nil {
		return "", err
	}
	if err := db.ClearCursor(s.Q, RecordOwedKey(agent)); err != nil {
		return "", err
	}
	if acct != "" {
		if err := db.SetAccount(s.Q, agent, acct, ""); err != nil {
			return "", err
		}
	}
	if _, err := db.NoteSessionStart(s.Q, agent, o.Cause, o.Resume, ifTrue(o.Resume, id), handoff, o.Prompt, o.By); err != nil {
		return "", err
	}
	return id, nil
}

// structured: the agent runs as a structured session.
func (m *Manager) structured(agent string) bool { return m.Run != nil && m.Run.Structured(agent) }

func (m *Manager) sessions() *Sessions {
	return &Sessions{Q: m.Q, Run: m.Run, Launch: m.Launch, NewID: m.NewID}
}

// hasSession: the agent has a session to talk to, which for a terminal agent is a terminal and for a structured one a live process.
func (m *Manager) hasSession(agent string, row *db.AgentState) bool {
	if m.structured(agent) {
		return m.Run.Alive(agent)
	}
	return row != nil && row.Terminal.String != ""
}

func (m *Manager) lockAgent(agent string) func() {
	m.once.Do(func() {
		if m.Locks == nil {
			m.Locks = &AgentLocks{}
		}
	})
	return m.Locks.Lock(agent)
}

// InterruptAgent stops the turn a structured agent is running, by control request.
func (m *Manager) InterruptAgent(agent string) (ok bool, said string, err error) {
	if !m.structured(agent) {
		return false, agent + " is a terminal agent; nothing to interrupt", nil
	}
	defer m.lockAgent(agent)()
	switch {
	case !m.Run.Alive(agent):
		return false, agent + " has no running session", nil
	case !m.Run.Busy(agent):
		return false, agent + " is not mid-turn", nil
	}
	if err := m.Run.Interrupt(agent); err != nil {
		return false, "", err
	}
	if _, err := db.AddEvent(m.Q, agent, "interrupted"); err != nil {
		return false, "", err
	}
	return true, agent + ": turn interrupted", nil
}

// endStructured closes a structured agent's session and records what brings it back. Close errors while a process of the session
// lives; then the agent is not reported stopped and the close is not retried every tick (close_failed holds ShouldClose off).
func (m *Manager) endStructured(agent, resume string, st SessionStatus) (bool, string) {
	if !m.Run.Alive(agent) {
		return false, "no session process; nothing to close"
	}
	if err := m.Run.Close(agent); err != nil {
		why := "the session would not end: " + err.Error()
		must(db.AddEvent(m.Q, agent, "close_failed", db.KV{K: "why", V: why}, db.KV{K: "pid", V: nil}))
		return false, why
	}
	m.recordClosed(agent, resume, st)
	return true, agent + ": session closed"
}

// recordClosed is what a session that has ended leaves: the closed mark and the way back.
func (m *Manager) recordClosed(agent, resume string, st SessionStatus) {
	check(db.SetClosed(m.Q, agent, resume))
	if row := must(db.GetAgentState(m.Q, agent)); row != nil && row.State == states.Running {
		check(db.SetAgentState(m.Q, agent, states.Idle, row.WakeAt.Int64, "session closed while idle"))
	}
	var sv any
	if st.SessionID != "" {
		sv = st.SessionID
	}
	must(db.AddEvent(m.Q, agent, "close", db.KV{K: "session", V: sv}, db.KV{K: "ctx_pct", V: st.CtxPct}))
	m.Log("  %s: session closed (resume %s)", agent, orUnknown(st.SessionID))
}

// waitIdle waits for the turn a forced close or restart must not cut off.
func (m *Manager) waitIdle(agent, handle string) bool {
	if m.structured(agent) {
		return m.Run.WaitIdle(agent, StructuredIdleWait)
	}
	return handle != "" && m.Term.WaitIdle(handle)
}

// StructuredIdleWait is how long a forced close waits for the running turn to end.
const StructuredIdleWait = 10 * time.Minute

// wakeStructured wakes a structured agent: a closed one is started again from its stored session (the same checks and the same account
// moves as any wake), a live one is sent its handoff as a message.
func (m *Manager) wakeStructured(agent string, row *db.AgentState, o WakeOpts) (bool, string) {
	wt := ""
	if row != nil {
		wt = m.Spawn.WorktreePath(row.Worktree.String)
	}
	switch {
	case isClosed(row):
		if wt == "" {
			return false, "no worktree to wake in"
		}
		return m.wakeClosed(agent, row, "", wt, o)
	case row == nil || must(db.GetHandoff(m.Q, agent)) == "":
		return false, "no handoff to resume from"
	case !m.Run.Alive(agent):
		// Nothing owns a process for it: the sweep records it closed, and the next wake starts it.
		return false, "no session process; the sweep records it closed first"
	case m.Run.Busy(agent):
		return false, "the session is mid-turn; not sending the handoff"
	}
	text, err := m.WakePrompt(agent, false, WakePromptIn{MovedTo: o.MovedTo})
	check(err)
	record, err := m.writeWakePrompt(agent, text, "handoff.txt")
	check(err)
	pointer := fmt.Sprintf("You were parked; this is a fresh session and your context is empty. Your handoff is in %s - read all of it. If it names a request or task, carry on with it; if it says none, wait - the next item is sent to you when it comes.", record)
	if err := m.Run.Send(agent, "wake:"+newHandoffID(), pointer); err != nil {
		return false, "the handoff was not delivered: " + err.Error()
	}
	st := m.Status.ReadForAgent(agent, m.Agents.CurrentAccount(m.Q, agent))
	must(db.NoteSessionStart(m.Q, agent, "handoff", false, st.SessionID, text, pointer, ""))
	check(db.ClearHandoff(m.Q, agent))
	m.Log("  %s: handoff sent to the running session", agent)
	return true, "handoff sent"
}

// launchStructured starts a closed structured agent again: its stored session resumed first, a fresh start the fallback. What waited
// for it is its first message. launchClosed is the terminal version and stays as it is.
func (m *Manager) launchStructured(agent string, row *db.AgentState, wt, session string, c closed2) (bool, string) {
	why0 := c.cause
	if why0 == "" {
		why0 = m.wakeCause(agent, row, c.movedTo)
	}
	cold := c.movedTo == "" && !c.warm && m.cacheExpiredAt(agent, session, m.now())
	if c.movedTo == "" && (cold || session == "") && !m.Launch.ChooseAccount(m.Q, agent) {
		return false, m.Launch.NoRoom(agent)
	}
	slot := c.movedTo
	if slot == "" {
		slot = m.Launch.AccountFor(m.Q, agent, session != "" && !cold)
	}
	if slot != "" {
		must(db.AddEvent(m.Q, agent, "wake_reserved", db.KV{K: "account", V: slot}))
	}
	took := false
	if cold && must(db.GetHandoff(m.Q, agent)) == "" {
		gaps := must(m.Handoff.Record(m.Q, agent, session))
		took = true
		if len(gaps) > 0 {
			w := "could not read " + strings.Join(gaps, ", ")
			must(db.AddEvent(m.Q, agent, "handoff", db.KV{K: "degraded", V: true}, db.KV{K: "session", V: nilIfEmpty(session)}, db.KV{K: "why", V: w}))
			m.Log("  %s: cold wake handoff degraded (%s)", agent, w)
		}
	}
	attempts := []bool{false}
	if session != "" && !cold {
		attempts = []bool{true, false}
	}
	var told func(string) error
	resumed, ok, why, id := false, false, "", ""
	for _, resume := range attempts {
		if m.Run.Alive(agent) {
			// The daemon owns every process, so a live one is a wake that raced this one; it is running, nothing is wrong with it.
			check(db.ClearClosed(m.Q, agent))
			return false, "a session is already running"
		}
		waitText, waitTold, got := "", func(string) error { return nil }, false
		if resume {
			waitText, waitTold, got = must3(m.Delivery.WaitingPrompt(m.Q, agent))
			if !got && !c.force {
				return false, "what was waiting for it is gone; nothing to resume it for"
			}
		}
		restartNote := ""
		if c.restartedBy != "" {
			restartNote = RestartNotice(c.restartedBy, resume, c.applying)
		}
		var err error
		text, err := m.WakePrompt(agent, resume, WakePromptIn{WasClosed: true, MovedTo: c.movedTo, Cold: cold, Waiting: waitText, RestartNote: restartNote})
		check(err)
		id, err = m.sessions().Start(agent, wt, StartOpts{Cause: why0, By: c.restartedBy, Resume: resume, Session: session, Account: c.movedTo, Prompt: text})
		if err == nil {
			resumed, ok = resume, true
			if got {
				told = waitTold
			}
			break
		}
		why = err.Error()
	}
	if !ok {
		m.wakeFailed(agent, session, why, took, false)
		return false, why
	}
	check(db.ResetCounter(m.Q, agent, "wake_failures"))
	if f := must(db.GetAgentState(m.Q, agent)); f != nil && isClosed(f) {
		check(db.ClearClosed(m.Q, agent))
	}
	// clear_closed drops the stored session, which for a structured agent is the id of the one running.
	check(db.SetResumeSession(m.Q, agent, id))
	if told != nil {
		// After clear_closed, which voids what a session was told.
		check(told(session))
	}
	payload := []db.KV{{K: "resumed_session", V: nilIfEmpty(ifTrue(resumed, session))}}
	if cold {
		payload = append(payload, db.KV{K: "cache_expired", V: true})
	}
	must(db.AddEvent(m.Q, agent, "wake", payload...))
	check(db.ClearHandoff(m.Q, agent))
	m.Log("  %s: started with its wake prompt%s", agent, ifTrue(resumed, " (session resumed)"))
	return true, "started with the wake prompt"
}
