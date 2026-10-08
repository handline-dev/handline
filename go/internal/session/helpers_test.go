package session

import (
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/term"
)

var _ Terminal = (*term.Term)(nil)

// fakeTerm answers each terminal from a table, and records what was pressed.
type fakeTerm struct {
	down     bool
	live     map[string]bool
	blockers map[string]*term.Blocker
	dialogs  map[string]*term.Dialog
	reads    int
	usage    map[string]bool
	vendor   string
	accepts  map[string]bool
	press    map[string][3]string // handle -> outcome, title, why of the next press
	pressed  []string
	cachedNB []time.Time
}

func newFakeTerm() *fakeTerm {
	return &fakeTerm{live: map[string]bool{}, blockers: map[string]*term.Blocker{}, dialogs: map[string]*term.Dialog{},
		usage: map[string]bool{}, accepts: map[string]bool{}, press: map[string][3]string{}, vendor: "claude"}
}

func (f *fakeTerm) Available() bool                      { return !f.down }
func (f *fakeTerm) Alive(h string) bool                  { return f.live[h] }
func (f *fakeTerm) LiveTerminals() map[string]bool       { return f.live }
func (f *fakeTerm) ScreenBlocker(h string) *term.Blocker { f.reads++; return f.blockers[h] }
func (f *fakeTerm) ScreenBlockerCached(h string, nb time.Time) *term.Blocker {
	f.cachedNB = append(f.cachedNB, nb)
	return f.blockers[h]
}
func (f *fakeTerm) UsageLimitOpen(h string) bool         { return f.usage[h] }
func (f *fakeTerm) DialogOnScreen(h string) *term.Dialog { return f.dialogs[h] }
func (f *fakeTerm) ScreenText(h string) (string, bool)   { return "line1\nline2", true }
func (f *fakeTerm) AcceptsTool(h, tool string) bool      { return f.accepts[tool] }
func (f *fakeTerm) VendorOf(string) string               { return f.vendor }
func (f *fakeTerm) next(kind, h string) (string, string, string) {
	f.pressed = append(f.pressed, kind+":"+h)
	p, ok := f.press[h]
	if !ok {
		return term.NotPressed, "", term.NoDialog
	}
	return p[0], p[1], p[2]
}
func (f *fakeTerm) DeclineDialog(h string) (string, string, string)   { return f.next("decline", h) }
func (f *fakeTerm) StopWorkAndExit(h string) (string, string, string) { return f.next("stop", h) }
func (f *fakeTerm) AcceptDialog(h, tool string) (string, string, string) {
	return f.next("accept "+tool, h)
}
func (f *fakeTerm) AnswerDialog(h string, choose func(string, *term.Dialog) int) (string, string, string) {
	if d := f.dialogs[h]; d != nil {
		choose(f.vendor, d)
	}
	return f.next("answer", h)
}

type fixture struct {
	*Env
	t    *testing.T
	term *fakeTerm
	now  int64
	logs []string
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	home := t.TempDir()
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	f := &fixture{t: t, term: newFakeTerm(), now: 1_800_000_000}
	f.Env = &Env{DB: db, Home: home, Term: f.term,
		Now: func() time.Time { return time.Unix(f.now, 0) },
		Log: func(format string, a ...any) { f.logs = append(f.logs, fmt.Sprintf(format, a...)) }}
	return f
}

func (f *fixture) exec(q string, args ...any) {
	f.t.Helper()
	if _, err := f.DB.Exec(q, args...); err != nil {
		f.t.Fatal(err)
	}
}

// agent adds an agent_state row.
func (f *fixture) agent(name, state, terminal string) *AgentRow {
	f.t.Helper()
	f.exec("INSERT INTO agent_state(agent, state, updated, terminal) VALUES(?,?,?,?)", name, state, f.now, nullable(terminal))
	row, err := f.GetAgentState(name)
	if err != nil || row == nil {
		f.t.Fatal(err)
	}
	return row
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func (f *fixture) event(agent, kind string) int64 {
	f.t.Helper()
	r, err := f.DB.Exec("INSERT INTO events(ts, agent, kind) VALUES(?,?,?)", f.now, agent, kind)
	if err != nil {
		f.t.Fatal(err)
	}
	id, _ := r.LastInsertId()
	return id
}

func timeAt(unix int64) time.Time { return time.Unix(unix, 0) }

type fakeDelivery struct {
	head       map[string]*Request
	changed    map[string]bool
	depth      map[string]int
	answers    map[string][]int64
	forgotten  map[string]*Forgotten
	nudgeAfter int64
	taskBlock  bool
	wall       map[string]string
	present    map[string]bool
	listed     map[string]*bool
	drawn      map[string]bool
}

func newFakeDelivery() *fakeDelivery {
	return &fakeDelivery{head: map[string]*Request{}, changed: map[string]bool{}, depth: map[string]int{}, answers: map[string][]int64{},
		forgotten: map[string]*Forgotten{}, nudgeAfter: 600, wall: map[string]string{}, present: map[string]bool{},
		listed: map[string]*bool{}, drawn: map[string]bool{}}
}

func (d *fakeDelivery) EffectiveHead(a string) *Request     { return d.head[a] }
func (d *fakeDelivery) SessionChanged(a string) bool        { return d.changed[a] }
func (d *fakeDelivery) QueueDepth(a string) int             { return d.depth[a] }
func (d *fakeDelivery) UnannouncedAnswers(a string) []int64 { return d.answers[a] }
func (d *fakeDelivery) ForgottenResolve(a string, _ *AgentRow, _ int64) *Forgotten {
	return d.forgotten[a]
}
func (d *fakeDelivery) NudgeAfterS() int64                         { return d.nudgeAfter }
func (d *fakeDelivery) HeadTaskBlocked(string, *Request) bool      { return d.taskBlock }
func (d *fakeDelivery) WallReason(a string, _ *AgentRow) string    { return d.wall[a] }
func (d *fakeDelivery) SessionPresent(a string) bool               { return d.present[a] }
func (d *fakeDelivery) TableListsSession(a string) *bool           { return d.listed[a] }
func (d *fakeDelivery) SessionStillDrawn(h string, wait bool) bool { return d.drawn[h] }

type reported struct{ agent, text, subject string }

type fakeAsker struct {
	reports   []reported
	decisions []reported
	nextID    int64
	states    map[int64]string
	resolved  map[int64]string
	open      map[string]int
}

func newFakeAsker() *fakeAsker {
	return &fakeAsker{nextID: 100, states: map[int64]string{}, resolved: map[int64]string{}, open: map[string]int{}}
}

func (a *fakeAsker) Report(agent, text, subject string) {
	a.reports = append(a.reports, reported{agent, text, subject})
}
func (a *fakeAsker) RaiseDecision(agent, text, subject string) int64 {
	a.nextID++
	a.decisions = append(a.decisions, reported{agent, text, subject})
	a.states[a.nextID] = "queued"
	return a.nextID
}
func (a *fakeAsker) ResolveDone(rid int64, text string) {
	a.resolved[rid] = text
	a.states[rid] = "landed"
}
func (a *fakeAsker) RequestState(rid int64) string    { return a.states[rid] }
func (a *fakeAsker) OpenDecisionsOf(agent string) int { return a.open[agent] }

type fakeLifecycle struct{ trust, bypass map[string]bool }

func (l *fakeLifecycle) AutoTrust(a string) bool  { return l.trust[a] }
func (l *fakeLifecycle) AutoBypass(a string) bool { return l.bypass[a] }

// blockedFixture is a world for the delivery blocks: one registered agent, "backend", running in terminal h1 with a request queued.
type blockedFixture struct {
	*fixture
	del  *fakeDelivery
	ask  *fakeAsker
	life *fakeLifecycle
	t0   int64
}

func newBlockedFixture(t *testing.T) *blockedFixture {
	f := newFixture(t)
	b := &blockedFixture{fixture: f, del: newFakeDelivery(), ask: newFakeAsker(), life: &fakeLifecycle{map[string]bool{}, map[string]bool{}}}
	f.Delivery, f.Asker, f.Lifecycle = b.del, b.ask, b.life
	f.Agents = func() []string { return []string{"backend", "frontend"} }
	f.agent("backend", Idle, "h1")
	f.term.live["h1"] = true
	b.del.head["backend"] = &Request{ID: 1}
	b.del.present["backend"] = true
	b.t0 = f.now
	return b
}

func (b *blockedFixture) at(secs int64) { b.now = b.t0 + secs }

func exitDialogBlocker() *term.Blocker {
	return &term.Blocker{Kind: term.Modal, Title: "Background work is running", Detail: "Background work is running - Exit and stop tasks / Stay"}
}
