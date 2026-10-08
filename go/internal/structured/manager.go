package structured

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sync"
	"time"
)

// Manager owns the structured sessions of the daemon, one per agent name, and wires each to the ledger, the permission broker and
// the event sink. It is the one place S3 (lifecycle) starts and stops them and S4 (send path) delivers through.
type Manager struct {
	DB          *sql.DB // the ledger and, with Broker.Store, the prompt rows
	Broker      *Broker
	Events      EventSink
	Now         func() time.Time
	InitTimeout time.Duration // zero is 30 s
	CLIVersion  string        // zero is PinnedCLI

	mu       sync.Mutex
	sessions map[string]*Session
	stopping map[string]bool // Stop is closing it: its exit is no failure
	starting map[string]bool // agents whose session is being started
}

// Has says whether agent has a live structured session.
func (m *Manager) Has(agent string) bool {
	s := m.Get(agent)
	return s != nil && s.ExitErr() == nil
}

// Get is agent's session, or nil.
func (m *Manager) Get(agent string) *Session {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.sessions[agent]
}

// Start checks the CLI version, starts agent's session and keeps it. A CLI that is not the pinned one is not started.
func (m *Manager) Start(ctx context.Context, agent string, l Launch) (*Session, error) {
	// Reserve the slot before the slow part, so two Starts for one agent never run two CLIs on one session.
	m.mu.Lock()
	old := m.sessions[agent]
	if m.starting[agent] || (old != nil && old.ExitErr() == nil) {
		m.mu.Unlock()
		return nil, fmt.Errorf("%s already has a structured session", agent)
	}
	if m.starting == nil {
		m.starting = map[string]bool{}
	}
	m.starting[agent] = true
	m.mu.Unlock()
	release := func() { m.mu.Lock(); delete(m.starting, agent); m.mu.Unlock() }
	defer release()
	if old != nil { // the process exited; prove its group is gone before another takes the session id
		if err := old.Close(); err != nil {
			return nil, err
		}
	}
	want := m.CLIVersion
	if want == "" {
		want = PinnedCLI
	}
	if err := CheckVersion(ctx, l.Path, want); err != nil {
		return nil, err
	}
	if l.Now == nil {
		l.Now = m.Now
	}
	if l.Ledger == nil && m.DB != nil {
		l.Ledger = &DBLedger{DB: m.DB, Agent: agent, Now: m.Now}
	}
	h := Handlers{OnEvent: func(f map[string]any) {
		if m.Events == nil {
			return
		}
		for _, ev := range Translate(f) {
			m.Events.Emit(agent, ev)
		}
	}}
	h.OnExit = func(err error) {
		m.mu.Lock()
		stopped := m.stopping[agent]
		m.mu.Unlock()
		if m.Events == nil {
			return
		}
		if err != nil && !stopped {
			m.Events.Emit(agent, Event{"error", map[string]any{"code": "exit", "text": err.Error()}})
		}
		m.Events.Emit(agent, Event{"session_end", nil}) // however it ended: the turn it left open is over
	}
	if m.Broker != nil {
		h.CanUseTool = m.Broker.CanUseTool(agent)
	}
	timeout := m.InitTimeout
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	s, err := Open(ctx, l, h, timeout)
	if err != nil {
		return nil, err
	}
	m.mu.Lock()
	if m.sessions == nil {
		m.sessions = map[string]*Session{}
	}
	delete(m.stopping, agent)
	m.sessions[agent] = s
	m.mu.Unlock()
	return s, nil
}

// Stop closes agent's session and forgets it when its process group is gone; otherwise it keeps it and returns the error.
func (m *Manager) Stop(agent string) error {
	s := m.Get(agent)
	if s == nil {
		return errors.New(agent + " has no structured session")
	}
	m.mu.Lock()
	if m.stopping == nil {
		m.stopping = map[string]bool{}
	}
	m.stopping[agent] = true
	m.mu.Unlock()
	if err := s.Close(); err != nil { // stopping stays set: OnExit may run after Close returns, and Start clears it
		return err
	}
	m.mu.Lock()
	delete(m.sessions, agent)
	m.mu.Unlock()
	return nil
}

// Send delivers through agent's session.
func (m *Manager) Send(agent, op, text string, d Delivery) (Receipt, error) {
	s := m.Get(agent)
	if s == nil {
		return Receipt{}, errors.New(agent + " has no structured session")
	}
	return s.Send(op, text, d)
}

// Withdraw takes back a held message of agent's session (chat.cancel of a queued message).
func (m *Manager) Withdraw(agent, op string) (Receipt, bool, error) {
	s := m.Get(agent)
	if s == nil {
		return Receipt{}, false, errors.New(agent + " has no structured session")
	}
	return s.Withdraw(op)
}

// Promote writes a held message of agent's session now (chat.send_now of a queued message).
func (m *Manager) Promote(agent, op string) (Receipt, error) {
	s := m.Get(agent)
	if s == nil {
		return Receipt{}, errors.New(agent + " has no structured session")
	}
	return s.Promote(op)
}

// Answer answers agent's waiting permission prompt (prompt.answer for a structured agent).
func (m *Manager) Answer(agent, prompt string, allow bool, message, by string) error {
	if m.Broker == nil {
		return ErrNoPrompt
	}
	return m.Broker.Answer(agent, prompt, allow, message, by)
}

// RetryingSince is when agent's session began retrying the model call (unix seconds), or 0 when it is not, or has no session.
func (m *Manager) RetryingSince(agent string) int64 {
	if s := m.Get(agent); s != nil {
		return s.RetryingSince()
	}
	return 0
}

// Interrupt stops the running turn of agent's session.
func (m *Manager) Interrupt(agent string) error {
	s := m.Get(agent)
	if s == nil {
		return errors.New(agent + " has no structured session")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return s.Interrupt(ctx)
}

// Kill ends agent's process group at once and forgets the session when the group is gone.
func (m *Manager) Kill(agent string) error {
	s := m.Get(agent)
	if s == nil {
		return errors.New(agent + " has no structured session")
	}
	if err := s.Kill(); err != nil {
		return err
	}
	m.mu.Lock()
	if m.sessions[agent] == s {
		delete(m.sessions, agent)
	}
	m.mu.Unlock()
	return nil
}
