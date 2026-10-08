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
