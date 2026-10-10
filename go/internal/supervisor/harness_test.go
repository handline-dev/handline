package supervisor

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
)

const teamYAML = `teams:
  eng: [alice, bob]
agents:
  alice: {}
  bob: {}
edges:
  - { from: "*", to: "human", kind: task }
  - { from: "*", to: "*", relation: peer, response: async }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
limits:
  max_chain_depth: 4
`

// fakeTerm is a terminal host: every handle shows a session, and what is typed is kept.
type fakeTerm struct {
	mu         sync.Mutex
	typed      []string
	typeable   bool
	blocker    *term.Blocker
	background string
	box        string
	boxKnown   bool
	sendFails  bool
	ctrlC      int
}

func newFakeTerm() *fakeTerm { return &fakeTerm{typeable: true, boxKnown: true} }

func (f *fakeTerm) Available() bool                        { return true }
func (f *fakeTerm) Missing() string                        { return "no terminal host" }
func (f *fakeTerm) Alive(string) bool                      { return true }
func (f *fakeTerm) TypeableNow(string) bool                { return f.typeable }
func (f *fakeTerm) ScreenBlocker(string) *term.Blocker     { return f.blocker }
func (f *fakeTerm) SessionOnScreen(string) (bool, bool)    { return true, true }
func (f *fakeTerm) ComposerOnScreen(string) (string, bool) { return f.box, f.boxKnown }
func (f *fakeTerm) IsTextTyped(box, text string) bool {
	return box != "" && strings.Contains(box, text)
}
func (f *fakeTerm) BackgroundWork(string) (string, bool) { return f.background, true }
func (f *fakeTerm) LastTail(string) []string             { return []string{"tail"} }
func (f *fakeTerm) Send(_, text string, _ bool) (bool, string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.typed = append(f.typed, text)
	return true, ""
}
func (f *fakeTerm) SendToSession(_, text string) (bool, string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.sendFails {
		return false, "pane gone"
	}
	f.typed = append(f.typed, text)
	return true, ""
}
func (f *fakeTerm) SendCtrlC(string)                   { f.ctrlC++ }
func (f *fakeTerm) ClearContext(string) (bool, string) { return true, "cleared" }
func (f *fakeTerm) ClearCommand(string) string         { return "/clear" }
func (f *fakeTerm) last() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.typed) == 0 {
		return ""
	}
	return f.typed[len(f.typed)-1]
}

type fakeObserver struct{ sessions map[string]bool }

func (o fakeObserver) LiveSessions() (map[string]bool, bool) { return o.sessions, true }

type fakeDialogs struct{}

func (fakeDialogs) PastDialog(db.Q, string, bool) (string, string) { return "", "" }
func (fakeDialogs) Reason(kind, detail string) string              { return kind + ": " + detail }
func (fakeDialogs) DecisionAfterS() int64                          { return 600 }

type fakeWaits struct{ limit map[string]bool }

func (fakeWaits) WaitingOnPrompt(db.Q, *db.AgentState) bool { return false }
func (w fakeWaits) AtLimit(_ db.Q, agent string) bool       { return w.limit[agent] }

type fakeIdle struct{}

func (fakeIdle) NoticeDigestS(string) int64              { return 900 }
func (fakeIdle) CloseTimeoutS(string) int64              { return 600 }
func (fakeIdle) Never() int64                            { return -1 }
func (fakeIdle) BgGraceS(string) int64                   { return 300 }
func (fakeIdle) ConfigSeconds(_ string, def int64) int64 { return def }
func (fakeIdle) FooterCount(string) int                  { return 1 }

type fakeSessions struct {
	live        string
	transcripts map[string]string
}

func (s *fakeSessions) LiveSession(db.Q, string) string         { return s.live }
func (s *fakeSessions) ContextUse(db.Q, string) (int64, int64)  { return 5000, 200000 }
func (s *fakeSessions) KeepsTranscripts(string) bool            { return len(s.transcripts) > 0 }
func (s *fakeSessions) FindTranscript(session, _ string) string { return s.transcripts[session] }
func (s *fakeSessions) MovableTrigger(kind string) bool         { return kind == "usage" }

type fakeLife struct{ started, woke []string }

func (l *fakeLife) StartForWork(_ db.Q, a string) string {
	l.started = append(l.started, a)
	return "started " + a
}
func (l *fakeLife) WakeAgent(_ db.Q, a string) (bool, string) {
	l.woke = append(l.woke, a)
	return true, "woken"
}

type fakeEval struct{ calls []string }

func (e *fakeEval) QueuedOrIdle(_ db.Q, a, why string) error {
	e.calls = append(e.calls, a+": "+why)
	return nil
}

type fakeStuck struct{ why string }

func (s fakeStuck) DeliveryBlock(db.Q, string) (string, bool) { return s.why, false }

// rig is an Env on a fresh home and database with every port faked.
type rig struct {
	*Env
	t        *testing.T
	home     string
	term     *fakeTerm
	sessions *fakeSessions
	life     *fakeLife
	eval     *fakeEval
	clock    int64
	logs     []string
}

func newRig(t *testing.T) *rig {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} {
		t.Setenv(k, "")
	}
	home, err := os.MkdirTemp("", "sup")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(home) })
	if err := os.WriteFile(filepath.Join(home, "team.yaml"), []byte(teamYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, a := range []string{"alice", "bob"} {
		dir := filepath.Join(home, "agents", a)
		os.MkdirAll(dir, 0o755)
		os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	}
	if err := copyTemplateDB(filepath.Join(home, "db.sqlite")); err != nil {
		t.Fatal(err)
	}
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	r := &rig{t: t, home: home, term: newFakeTerm(), sessions: &fakeSessions{live: "s1"}, life: &fakeLife{}, eval: &fakeEval{}, clock: 100000}
	db.Now = func() int64 { return r.clock }
	t.Cleanup(func() { db.Now = func() int64 { return time.Now().Unix() } })
	r.Env = &Env{DB: d, Home: home, Now: func() time.Time { return time.Unix(r.clock, 0) }, Sleep: func(d time.Duration) { r.clock += int64(d / time.Second) }}
	r.Env.Log = func(f string, a ...any) { r.logs = append(r.logs, fmt.Sprintf(f, a...)) }
	r.Env.Ports = Ports{Agents: &sessionAgents{r: r, live: map[string]bool{}}, Terminal: r.term, Observer: fakeObserver{map[string]bool{"alice": true, "bob": true}}, Dialogs: fakeDialogs{}, Waits: fakeWaits{limit: map[string]bool{}},
		Idle: fakeIdle{}, Sessions: r.sessions, Lifecycle: r.life, Evaluate: r.eval, Stuck: fakeStuck{}, Proposals: fakeProposals{}}
	return r
}

// sessionAgents is the one way a session is told anything: what is sent is kept in the rig's term, as what the agent was told.
type sessionAgents struct {
	r    *rig
	live map[string]bool
}

func (a *sessionAgents) Structured(string) bool { return true }
func (a *sessionAgents) Live(agent string) bool { return a.live[agent] }
func (a *sessionAgents) Send(_, _, text string, _ structured.Delivery) (structured.Receipt, error) {
	a.r.term.mu.Lock()
	defer a.r.term.mu.Unlock()
	a.r.term.typed = append(a.r.term.typed, text)
	return structured.Receipt{State: structured.Sent}, nil
}
func (a *sessionAgents) Withdraw(string, string) (structured.Receipt, bool, error) {
	return structured.Receipt{}, false, nil
}
func (a *sessionAgents) Promote(string, string) (structured.Receipt, error) {
	return structured.Receipt{}, nil
}

// agentUp gives an agent a terminal and an open session that takes what it is told.
func (r *rig) agentUp(name string) {
	r.t.Helper()
	r.Agents.(*sessionAgents).live[name] = true
	if err := db.SetTerminal(r.DB, name, "%"+name, "/w/"+name); err != nil {
		r.t.Fatal(err)
	}
}

func (r *rig) must(err error) {
	r.t.Helper()
	if err != nil {
		r.t.Fatal(err)
	}
}

func (r *rig) logged(sub string) bool {
	for _, l := range r.logs {
		if strings.Contains(l, sub) {
			return true
		}
	}
	return false
}

var _ = sql.ErrNoRows

var blockerOf = term.Blocker{Kind: "dialog", Title: "Trust this folder?"}

var (
	templateOnce sync.Once
	templatePath string
	templateErr  error
)

// copyTemplateDB gives each test a migrated database without migrating it again: the migrations are most of a test's time.
func copyTemplateDB(dst string) error {
	templateOnce.Do(func() {
		dir, err := os.MkdirTemp("", "suptemplate")
		if err != nil {
			templateErr = err
			return
		}
		templatePath = filepath.Join(dir, "db.sqlite")
		d, err := store.Open(templatePath)
		if err != nil {
			templateErr = err
			return
		}
		templateErr = d.Close()
	})
	if templateErr != nil {
		return templateErr
	}
	b, err := os.ReadFile(templatePath)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, b, 0o644)
}

func lockTerminal(home, agent string) *lock.Hold { return lock.TakeTerminal(home, agent) }

// flipTerm shows no blocker for the first `after` reads and then the blocker.
type flipTerm struct {
	*fakeTerm
	after int
	then  *term.Blocker
	reads int
}

func (f *flipTerm) ScreenBlocker(h string) *term.Blocker {
	f.reads++
	if f.reads > f.after {
		return f.then
	}
	return f.fakeTerm.ScreenBlocker(h)
}

type fakeProposals struct{}

func (fakeProposals) OpenProposalOf(int64) string                       { return "" }
func (fakeProposals) Answered(db.Q, string, *db.Request, string) string { return "" }

func team0() team.Decision { return team.Decision{Allowed: true, Response: "async"} }

func readInbox(r *rig, rid int64) ([]byte, error) {
	return os.ReadFile(filepath.Join(r.home, "agents", "bob", "inbox", inboxName(rid)))
}
