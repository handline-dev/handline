package subscriptions

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
)

type fakeAsker struct {
	t       *testing.T
	conn    db.Q
	raised  []string
	openers []string
	nextTID int64
}

func (a *fakeAsker) RaiseDecision(q db.Q, agent, text, reason string) (int64, error) {
	a.raised = append(a.raised, text)
	a.openers = append(a.openers, agent)
	a.nextTID++
	if _, err := q.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(?, 't', 'human', ?, 1)", a.nextTID, agent); err != nil {
		a.t.Fatal(err)
	}
	if _, err := q.Exec("INSERT INTO task_requests(task_id, request_id) VALUES(?, ?)", a.nextTID, 500+a.nextTID); err != nil {
		a.t.Fatal(err)
	}
	return 500 + a.nextTID, nil
}

type fakeTasks struct {
	closed []string
	ok     bool
}

func (f *fakeTasks) MarkDoneByFounder(_ db.Q, tid int64, reason string) (bool, string, error) {
	f.closed = append(f.closed, fmt.Sprintf("T%d: %s", tid, reason))
	return f.ok, "refused for a reason", nil
}

type fakePlans struct {
	recorded map[string]string
	refresh  map[string]string
	fail     error
	asked    []string
}

func (p *fakePlans) Recorded(acct string) (string, bool) { v, ok := p.recorded[acct]; return v, ok }
func (p *fakePlans) Refresh(acct string) (string, error) {
	p.asked = append(p.asked, acct)
	return p.refresh[acct], p.fail
}

type world struct {
	*Env
	t     *testing.T
	asker *fakeAsker
	tasks *fakeTasks
	plans *fakePlans
	now   time.Time
	logs  []string
}

func newWorld(t *testing.T) *world {
	t.Helper()
	home := t.TempDir()
	conn, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	w := &world{t: t, now: time.Date(2026, 10, 10, 12, 0, 0, 0, time.Local), tasks: &fakeTasks{ok: true},
		plans: &fakePlans{recorded: map[string]string{}, refresh: map[string]string{}}}
	w.asker = &fakeAsker{t: t, conn: conn}
	db.Now = func() int64 { return w.now.Unix() }
	t.Cleanup(func() { db.Now = func() int64 { return time.Now().Unix() } })
	w.Env = &Env{DB: conn, Home: home, Asker: w.asker, Tasks: w.tasks, Plans: w.plans, Now: func() time.Time { return w.now },
		Log: func(f string, a ...any) { w.logs = append(w.logs, fmt.Sprintf(f, a...)) }}
	return w
}

func (w *world) account(name string) {
	w.t.Helper()
	if err := os.MkdirAll(filepath.Join(w.Home, "accounts", name), 0o755); err != nil {
		w.t.Fatal(err)
	}
}

func (w *world) agent(name, account string) {
	w.t.Helper()
	dir := filepath.Join(w.Home, "agents", name)
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte("name: "+name+"\n"), 0o644)
	if account != "" {
		if _, err := w.DB.Exec("INSERT INTO agent_state(agent, state, updated, account) VALUES(?, 'IDLE', 1, ?)", name, account); err != nil {
			w.t.Fatal(err)
		}
	}
}

func (w *world) config(text string) {
	w.t.Helper()
	if err := os.WriteFile(filepath.Join(w.Home, "config.yaml"), []byte(text), 0o644); err != nil {
		w.t.Fatal(err)
	}
}

func (w *world) sweep() {
	w.t.Helper()
	if err := w.SweepEndings(); err != nil {
		w.t.Fatal(err)
	}
}

func (w *world) meta(name string) string {
	v, _, _ := db.Meta(w.DB, name)
	return v
}

const endingConfig = "month_cycle_acme: true\nsub_end_acme: \"2026-10-12\"\n"

func TestTheFounderIsToldOnceInsideTheWarningWindow(t *testing.T) {
	w := newWorld(t)
	w.account("acme")
	w.agent("alpha", "other")
	w.agent("beta", "acme")
	w.config(endingConfig)

	w.sweep()
	if len(w.asker.raised) != 1 || w.asker.openers[0] != "beta" {
		t.Fatalf("raised %q by %q", w.asker.raised, w.asker.openers)
	}
	body := w.asker.raised[0]
	for _, want := range []string{"acme subscription ends 2026-10-12\n\n", "ends 2026-10-12 (its last usable day), in 60.0 h.", "`hl account cycle acme --end YYYY-MM-DD`"} {
		if !strings.Contains(body, want) {
			t.Errorf("notice lacks %q:\n%s", want, body)
		}
	}
	if w.meta("sub_ending_told:acme") != "2026-10-12" || w.meta("sub_ending_task:acme") != "1" {
		t.Errorf("told %q task %q", w.meta("sub_ending_told:acme"), w.meta("sub_ending_task:acme"))
	}
	if !strings.Contains(strings.Join(w.logs, "\n"), "acme: subscription ends 2026-10-12 - founder told") {
		t.Errorf("logs %q", w.logs)
	}
	w.now = w.now.Add(time.Hour)
	w.sweep()
	if len(w.asker.raised) != 1 || len(w.tasks.closed) != 0 {
		t.Errorf("told again: %d, closed %q", len(w.asker.raised), w.tasks.closed)
	}
}

func TestNothingIsSaidOutsideTheWindowOrWithoutACycle(t *testing.T) {
	w := newWorld(t)
	w.account("acme")
	w.account("plain")
	w.config("month_cycle_acme: true\nsub_end_acme: \"2026-10-20\"\n")
	w.sweep()
	if len(w.asker.raised) != 0 {
		t.Fatalf("raised %q", w.asker.raised)
	}
	w.now = time.Date(2026, 10, 18, 12, 0, 0, 0, time.Local) // 2 days and 12 hours before it is over
	w.config("month_cycle_acme: true\nsub_end_acme: \"2026-10-20\"\ncycle_warn_days: 1\n")
	w.sweep()
	if len(w.asker.raised) != 0 {
		t.Fatalf("a one-day window told at two days: %q", w.asker.raised)
	}
	// With no agent at all the notice is opened by the daemon.
	w.config("month_cycle_acme: true\nsub_end_acme: \"2026-10-20\"\n")
	w.sweep()
	if len(w.asker.raised) != 1 || w.asker.openers[0] != Sender {
		t.Errorf("raised %q by %q", w.asker.raised, w.asker.openers)
	}
}

func TestANoticeClosesWhenTheDateChangesTheCycleEndsOrTheAccountHasEnded(t *testing.T) {
	cases := []struct {
		name, config string
		advance      time.Duration
		why          string
	}{
		{"a new end date", "month_cycle_acme: true\nsub_end_acme: \"2026-10-30\"\n", 0, "the account's end date changed"},
		{"the cycle turned off", "month_cycle_acme: false\nsub_end_acme: \"2026-10-12\"\n", 0, "the month cycle was turned off"},
		{"the account ended", endingConfig, 60 * time.Hour, "the subscription ended; the account is shown as ended"},
	}
	for _, c := range cases {
		w := newWorld(t)
		w.account("acme")
		w.agent("beta", "acme")
		w.config(endingConfig)
		w.sweep()
		if w.meta("sub_ending_task:acme") != "1" {
			t.Fatalf("%s: not told", c.name)
		}
		w.config(c.config)
		w.now = w.now.Add(c.advance)
		w.sweep()
		if len(w.tasks.closed) != 1 || w.tasks.closed[0] != "T1: closed by the daemon: "+c.why {
			t.Errorf("%s: closed %q", c.name, w.tasks.closed)
		}
		if w.meta("sub_ending_task:acme") != "" {
			t.Errorf("%s: the task stayed remembered", c.name)
		}
		if c.name == "the cycle turned off" && w.meta("sub_ending_told:acme") != "" {
			t.Errorf("%s: turned on again, the same date would stay old news", c.name)
		}
	}
	// A new end date inside the window is told again as a new notice.
	w := newWorld(t)
	w.account("acme")
	w.config(endingConfig)
	w.sweep()
	w.config("month_cycle_acme: true\nsub_end_acme: \"2026-10-11\"\n")
	w.sweep()
	if len(w.asker.raised) != 2 || len(w.tasks.closed) != 1 {
		t.Errorf("raised %d closed %q", len(w.asker.raised), w.tasks.closed)
	}
}

func TestANoticeTheTaskLayerRefusesIsLoggedAndForgotten(t *testing.T) {
	w := newWorld(t)
	w.account("acme")
	w.tasks.ok = false
	w.config(endingConfig)
	w.sweep()
	w.config("month_cycle_acme: false\n")
	w.sweep()
	if got := w.logs[len(w.logs)-1]; got != "  acme: notice T1: refused for a reason" {
		t.Errorf("log %q", got)
	}
	if w.meta("sub_ending_task:acme") != "" {
		t.Error("a refused notice is asked again every tick")
	}
}

func TestAConfigThatCannotBeReadLeavesTheNoticesAlone(t *testing.T) {
	w := newWorld(t)
	w.account("acme")
	w.config(endingConfig)
	w.sweep()
	w.config("month_cycle_acme: [oops\n")
	w.sweep()
	if len(w.tasks.closed) != 0 || w.meta("sub_ending_task:acme") != "1" {
		t.Errorf("closed %q", w.tasks.closed)
	}
	if got := w.logs[len(w.logs)-1]; got != "  subscriptions: config.yaml unreadable - notices left as they are" {
		t.Errorf("log %q", got)
	}
}

func TestAChangedPlanIsAnEventAndTheFirstSightingIsNot(t *testing.T) {
	w := newWorld(t)
	w.account("acme")
	w.plans.recorded["acme"] = "pro"
	w.config("")
	if err := w.SweepPlans(); err != nil {
		t.Fatal(err)
	}
	w.WaitForPlanAsk()
	var n int
	w.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'plan_changed'").Scan(&n)
	if n != 0 || w.meta("plan_seen:acme") != "pro" {
		t.Fatalf("first sighting: %d events, seen %q", n, w.meta("plan_seen:acme"))
	}
	w.plans.recorded["acme"] = "max"
	w.SweepPlans()
	w.WaitForPlanAsk()
	var payload, account string
	w.DB.QueryRow("SELECT payload, account FROM events WHERE kind = 'plan_changed'").Scan(&payload, &account)
	if payload != `{"from": "pro", "to": "max"}` || account != "acme" || w.meta("plan_seen:acme") != "max" {
		t.Errorf("payload %q account %q", payload, account)
	}
	if !strings.Contains(strings.Join(w.logs, "\n"), "acme: plan pro -> max") {
		t.Errorf("logs %q", w.logs)
	}
}

func TestOneAccountIsAskedItsPlanAnHourAtATimeOldestFirst(t *testing.T) {
	w := newWorld(t)
	w.account("acme")
	w.account("zeta")
	w.plans.refresh["acme"] = "pro"
	w.SweepPlans()
	w.WaitForPlanAsk()
	w.SweepPlans() // inside the hour: nobody
	w.WaitForPlanAsk()
	if strings.Join(w.plans.asked, ",") != "acme,zeta" && strings.Join(w.plans.asked, ",") != "acme" {
		t.Fatalf("asked %q", w.plans.asked)
	}
	first := len(w.plans.asked)
	w.now = w.now.Add(PlanRefreshS * time.Second)
	w.SweepPlans()
	w.WaitForPlanAsk()
	if len(w.plans.asked) != first+1 {
		t.Fatalf("asked %q", w.plans.asked)
	}
	// The zeta account read nothing: said so; a failure is said too.
	log := strings.Join(w.logs, "\n")
	if !strings.Contains(log, "zeta: no plan recorded and none read (not logged in, or auth status failed)") {
		t.Errorf("logs %q", w.logs)
	}
	w.plans.fail = fmt.Errorf("boom")
	w.now = w.now.Add(PlanRefreshS * time.Second)
	w.SweepPlans()
	w.WaitForPlanAsk()
	if got := w.logs[len(w.logs)-1]; got != "  zeta: plan question failed: boom" && got != "  acme: plan question failed: boom" {
		t.Errorf("log %q", got)
	}
}

func TestNoAccountsMeansNothingToAsk(t *testing.T) {
	w := newWorld(t)
	if err := w.Sweep(); err != nil {
		t.Fatal(err)
	}
	w.WaitForPlanAsk()
	if len(w.plans.asked) != 0 {
		t.Errorf("asked %q", w.plans.asked)
	}
}
