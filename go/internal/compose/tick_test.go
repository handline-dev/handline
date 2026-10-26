package compose

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
)

func TestAnAgentWithTheRenewalSwitchOnIsNotStalledForWantingACacheClock(t *testing.T) {
	p, _, _, wt := wired(t)
	d := p.db
	os.WriteFile(filepath.Join(p.home, "agents", "alice", "agent.yaml"), []byte("vendor: claude\nstructured: true\ncache_renewal: true\nidle_fold: true\n"), 0o644)
	// A session that is open and idle, with a status naming a transcript that is not there: the clock is unreadable, so no renewal is due.
	if err := dbpkg.SetResumeSession(d, "alice", "s1"); err != nil {
		t.Fatal(err)
	}
	d.Exec("UPDATE agent_state SET closed_at = NULL, state = 'IDLE' WHERE agent = 'alice'")
	_ = wt
	tk := p.Tick(cfgOf(p), TickConfig{})
	if tk.Tick.Cost == nil || tk.Tick.Cost.Ports == nil {
		t.Fatal("the cost pilot has no ports")
	}
	for i := 0; i < 4; i++ {
		if _, err := tk.Tick.Run(nil); err != nil {
			t.Fatal(err)
		}
	}
	row, _ := dbpkg.GetAgentState(d, "alice")
	if row.State == "STALLED" || row.FailedAttempts != 0 {
		t.Fatalf("the agent was stalled by the pilot: %+v", row)
	}
	cp := costPorts{}
	if cp.ReadCache("") != nil || cp.ReadCache("/nonexistent") != nil {
		t.Fatal("a missing transcript has a clock")
	}
}

func TestShutdownWaitsForTheTickInHandBeforeTheDatabaseCloses(t *testing.T) {
	p, _, _, _ := wired(t)
	stop := make(chan struct{})
	tk := p.Tick(cfgOf(p), TickConfig{Stop: stop})
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	tk.Tick.Passes = []tick.NamedSweep{{Name: "slow", Run: func(dbpkg.Q, int64) error {
		once.Do(func() { close(entered) })
		<-release // a close or a wake in the middle
		return nil
	}}}
	done := tk.Start()
	<-entered
	close(stop)
	select {
	case <-done:
		t.Fatal("the loop returned while a tick was still running")
	case <-time.After(200 * time.Millisecond):
	}
	close(release)
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("the loop did not return after the tick finished")
	}
}

func TestTheTickRunsItsPassesWithoutHarmingAnAgent(t *testing.T) {
	p, _, _, _ := wired(t)
	tk := p.Tick(cfgOf(p), TickConfig{})
	if len(tk.Tick.Passes) != 12 {
		t.Fatalf("passes: %d", len(tk.Tick.Passes))
	}
	var logs []string
	cfg := cfgOf(p)
	cfg.Log = func(format string, a ...any) { logs = append(logs, fmt.Sprintf(format, a...)) }
	tk = p.Tick(cfg, TickConfig{})
	for i := 0; i < 3; i++ {
		if _, err := tk.Tick.Run(nil); err != nil {
			t.Fatal(err)
		}
	}
	for _, l := range logs {
		if strings.Contains(l, "timebox:") || strings.Contains(l, "schedules:") || strings.Contains(l, "notice-digest:") || strings.Contains(l, "task-queue:") || strings.Contains(l, "task-holds:") || strings.Contains(l, "subscriptions:") || strings.Contains(l, "wait-for:") || strings.Contains(l, "apply-settings:") || strings.Contains(l, "hr-held:") {
			t.Fatalf("a pass failed: %s", l)
		}
	}
}

func TestTheFirstTickAfterMidnightRollsTheDigestOfAnAgent(t *testing.T) {
	p, _, _, _ := wired(t)
	dir := filepath.Join(p.home, "agents", "alice", "journal")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "2026-10-09.md"), []byte("# alice · 2026-10-09\nupdated: 10:00\n\n## In progress\n- the port\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 10, 9, 23, 59, 0, 0, time.Local)
	cfg := cfgOf(p)
	cfg.Now = func() time.Time { return now }
	tk := p.Tick(cfg, TickConfig{Terminals: true})
	run := func() {
		t.Helper()
		if _, err := tk.Tick.Run(nil); err != nil {
			t.Fatal(err)
		}
	}
	run() // the first tick only learns the day
	if _, err := os.Stat(filepath.Join(dir, "2026-10-10.md")); err == nil {
		t.Fatal("rolled before midnight")
	}
	now = now.Add(2 * time.Minute)
	run()
	raw, err := os.ReadFile(filepath.Join(dir, "2026-10-10.md"))
	if err != nil || !strings.Contains(string(raw), "the port") {
		t.Fatalf("today's digest: %v %q", err, raw)
	}
}

func TestTheLastContextIsReadPastALineLongerThanAScannerAllows(t *testing.T) {
	path := filepath.Join(t.TempDir(), "s.jsonl")
	small := `{"type":"assistant","message":{"model":"m","usage":{"input_tokens":1}}}` + "\n"
	big := `{"type":"assistant","message":{"model":"m2","usage":{"input_tokens":7,"cache_read_input_tokens":3},"content":"` + strings.Repeat("x", 2<<20) + `"}}` + "\n"
	os.WriteFile(path, []byte(small+big), 0o644)
	if used, model, ok := lastContext(path); !ok || used != 10 || model != "m2" {
		t.Fatalf("%d %q %v", used, model, ok)
	}
}

func TestTheLoopEndsForARestartWhenDevModeFindsAChangedExecutableThatStarts(t *testing.T) {
	p, _, _, _ := wired(t)
	exe := filepath.Join(t.TempDir(), "handlined")
	os.WriteFile(exe, []byte("old"), 0o755)
	clock := time.Unix(1_800_000_000, 0)
	cfg := cfgOf(p)
	cfg.Now = func() time.Time { return clock }
	tk := p.Tick(cfg, TickConfig{Stop: make(chan struct{})})
	tk.dev.Exe, tk.dev.Check = exe, func(string) error { return nil }
	tk.dev.Baseline()
	tk.Loop.Sleep = func(time.Duration) bool { clock = clock.Add(31 * time.Second); return true }
	os.WriteFile(paths.ConfigFile(p.home), []byte("dev_mode: true\n"), 0o644)
	os.WriteFile(exe, []byte("a new build"), 0o755) // another size: a changed executable
	select {
	case <-tk.Start():
	case <-time.After(20 * time.Second):
		t.Fatal("the loop never ended")
	}
	if !tk.Restarting() {
		t.Fatal("it ended without asking to restart")
	}
}

func TestTheTickRelinksAccountsToSharedItemsAddedAfterTheyWereRegistered(t *testing.T) {
	p, _, _, _ := wired(t)
	cdir, _ := team.AccountConfigDir(p.home, "a1")
	os.MkdirAll(filepath.Join(p.home, "shared", ".claude"), 0o755)
	os.WriteFile(filepath.Join(p.home, "shared", ".claude", "CLAUDE.md"), []byte("x"), 0o644)
	tk := p.Tick(cfgOf(p), TickConfig{})
	got, err := tk.Tick.Sweeps.RelinkAccounts()
	if err != nil || len(got["a1"]) == 0 {
		t.Fatalf("%v %v", got, err)
	}
	if _, err := os.Lstat(filepath.Join(cdir, "CLAUDE.md")); err != nil {
		t.Fatal("not linked")
	}
}

func TestTheLoopRunsTheDescriptorCheckAndLogRotationAfterEachTick(t *testing.T) {
	p, _, _, _ := wired(t)
	var mu sync.Mutex
	var logged []string
	cfg := cfgOf(p)
	cfg.Log = func(f string, a ...any) { mu.Lock(); logged = append(logged, fmt.Sprintf(f, a...)); mu.Unlock() }
	log := filepath.Join(p.home, "logs", "daemon.err.log")
	os.MkdirAll(filepath.Dir(log), 0o755)
	os.WriteFile(log, make([]byte, 11<<20), 0o644)
	tk := p.Tick(cfg, TickConfig{Stop: make(chan struct{})})
	tk.Loop.MaxTicks = 1
	<-tk.Start()
	if st, _ := os.Stat(log); st.Size() != 0 {
		t.Fatal("the log over its limit was not rotated")
	}
	mu.Lock()
	defer mu.Unlock()
	if !strings.Contains(strings.Join(logged, "\n"), "fds: ") {
		t.Fatalf("no descriptor count: %q", logged)
	}
}

// passRuns reports whether the named pass reaches the db: on a closed one it errors or panics, and a gated-off pass returns nil.
func passRuns(t *testing.T, cutover bool, name string) (ran bool) {
	p, _, _, _ := wired(t)
	tk := p.Tick(cfgOf(p), TickConfig{Cutover: cutover})
	p.db.Close()
	for _, s := range tk.Tick.Passes {
		if s.Name == name {
			defer func() {
				if recover() != nil {
					ran = true
				}
			}()
			return s.Run(p.db, time.Now().Unix()) != nil
		}
	}
	t.Fatalf("no pass %s", name)
	return
}

func TestPassesPythonStillSweepsRunOnlyFromTheCutover(t *testing.T) {
	for _, name := range []string{"task-queue", "task-holds", "subscriptions", "notice-digest", "wait-for"} {
		if passRuns(t, false, name) {
			t.Errorf("%s ran before the cutover", name)
		}
		if !passRuns(t, true, name) {
			t.Errorf("%s did not run from the cutover", name)
		}
	}
}

func TestTheSubscriptionsPassRunsOnlyFromTheCutover(t *testing.T) {
	for _, cutover := range []bool{false, true} {
		p, _, _, _ := wired(t)
		t.Setenv("PATH", t.TempDir()) // the plan question must not reach a real claude
		tk := p.Tick(cfgOf(p), TickConfig{Cutover: cutover})
		for _, s := range tk.Tick.Passes {
			if s.Name == "subscriptions" {
				if err := s.Run(p.db, time.Now().Unix()); err != nil {
					t.Fatal(err)
				}
			}
		}
		var n int
		p.db.QueryRow("SELECT COUNT(*) FROM cursors WHERE name LIKE 'plan_checked:%'").Scan(&n)
		if (n > 0) != cutover {
			t.Fatalf("cutover=%v: %d plan cursors", cutover, n)
		}
	}
}

func TestTheTickRunsTheNoticeSweepAsALevelOfPersistedState(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.db
	key := "stopped_told:fatal:alice"
	tick := func() {
		t.Helper()
		if _, err := p.Tick(cfgOf(p), TickConfig{Cutover: true}).Tick.Run(nil); err != nil { // a new tick each time: nothing is remembered in memory
			t.Fatal(err)
		}
	}
	if err := dbpkg.SetAgentState(d, "alice", states.Stalled, 0, "boom"); err != nil {
		t.Fatal(err)
	}
	tick()
	if v, _ := dbpkg.Cursor(d, key); v != 1 {
		t.Fatalf("a FATAL agent is marked as told once the tick has run: %d", v)
	}
	var said int
	count := func() {
		d.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'notified'").Scan(&said)
	}
	count()
	before := said
	tick()
	if count(); said != before {
		t.Fatal("the same spell is told once, however many ticks follow")
	}
	if err := dbpkg.SetAgentState(d, "alice", states.Idle, 0, ""); err != nil {
		t.Fatal(err)
	}
	tick()
	if v, _ := dbpkg.Cursor(d, key); v != 0 {
		t.Fatalf("the mark ends with the spell: %d", v)
	}
}

func TestTheStartRunsReconcileMigrateStartWaitingReloadInThatOrder(t *testing.T) {
	var calls []string
	ports := startPorts{
		Reconcile:    func() ([]string, error) { calls = append(calls, "reconcile"); return nil, nil },
		Migrate:      func() error { calls = append(calls, "migrate"); return nil },
		StartWaiting: func() []string { calls = append(calls, "start-waiting"); return nil },
		Reload:       func() error { calls = append(calls, "reload"); return nil },
	}
	runStartup(func(string) {}, ports)
	if got := strings.Join(calls, ","); got != "reconcile,migrate,start-waiting,reload" {
		t.Fatalf("%s", got)
	}
}

func TestAReconcileThatFailsEndsTheStartBeforeAnyAgentIsStarted(t *testing.T) {
	var calls, logs []string
	ports := startPorts{
		Reconcile: func() ([]string, error) {
			calls = append(calls, "reconcile")
			return []string{"x: resumed"}, fmt.Errorf("database is locked")
		},
		Migrate:      func() error { calls = append(calls, "migrate"); return nil },
		StartWaiting: func() []string { calls = append(calls, "start-waiting"); return nil },
		Reload:       func() error { calls = append(calls, "reload"); return nil },
	}
	runStartup(func(s string) { logs = append(logs, s) }, ports)
	if strings.Join(calls, ",") != "reconcile" {
		t.Fatalf("%v", calls)
	}
	if all := strings.Join(logs, "\n"); !strings.Contains(all, "database is locked") || !strings.Contains(all, "x: resumed") {
		t.Fatalf("%q", logs)
	}
}

func TestBeforeTheCutoverTheTickSweepsNobodysSchedules(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.db
	os.WriteFile(filepath.Join(p.home, "agents", "alice", "agent.yaml"),
		[]byte("vendor: claude\nstructured: true\nschedules: '[{\"cron\": \"* * * * *\", \"message\": \"check the queue\"}]'\n"), 0o644)
	tk := p.Tick(cfgOf(p), TickConfig{})
	t0 := time.Now().Unix()
	t0 -= t0 % 60
	for _, s := range tk.Tick.Passes {
		if s.Name == "schedules" {
			s.Run(d, t0)
			s.Run(d, t0+120)
		}
	}
	var n int
	d.QueryRow("SELECT COUNT(*) FROM requests WHERE requester = 'schedule'").Scan(&n)
	var c int
	d.QueryRow("SELECT COUNT(*) FROM cursors WHERE name LIKE 'schedule_%'").Scan(&c)
	if n != 0 || c != 0 {
		t.Fatalf("Python still sweeps them: %d messages, %d cursors", n, c)
	}
}

func TestTheDiskWatchEntryRaisesOneCardFromTheTaskBoard(t *testing.T) {
	p, _, _, _ := wired(t)
	cfg := cfgOf(p)
	cfg.DiskFree = func(string) (int64, int64, error) { return 9 << 30, 500 << 30, nil }
	tk := p.Tick(cfg, TickConfig{Stop: make(chan struct{})})
	var watch *tick.NamedSweep
	for i, h := range tk.Loop.Housekeeping {
		if h.Name == "disk watch" {
			watch = &tk.Loop.Housekeeping[i]
		}
	}
	if watch == nil {
		t.Fatal("the loop has no disk watch")
	}
	told := func() int {
		var n int
		if err := p.db.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'disk_low'").Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	now := time.Now().Unix()
	if err := watch.Run(p.db, now); err != nil {
		t.Fatal(err)
	}
	if told() != 1 {
		t.Fatalf("one disk_low event after a low volume: %d", told())
	}
	var inbox, tasks int
	p.db.QueryRow("SELECT COUNT(*) FROM requests WHERE target = ?", dbpkg.Human).Scan(&inbox)
	p.db.QueryRow("SELECT COUNT(*) FROM tasks WHERE owner = ?", dbpkg.Human).Scan(&tasks)
	if inbox != 0 || tasks != 0 {
		t.Fatalf("a low volume is a notification, not a message (%d) or a task (%d)", inbox, tasks)
	}
	if err := watch.Run(p.db, now+601); err != nil {
		t.Fatal(err)
	}
	if told() != 1 {
		t.Fatalf("a second check at the same level told again: %d", told())
	}
}

func stateOf(view string) string {
	var v struct {
		State string `json:"state"`
	}
	json.Unmarshal([]byte(view), &v)
	return v.State
}

func TestTheNoticeSweepsRunInTheGoTickOnlyFromTheCutover(t *testing.T) {
	for _, cutover := range []bool{false, true} {
		p, _, _, _ := wired(t)
		d := p.db
		// alice is closed, so no digest is typed to her: the stopped sweep's mark says whether the sweeps ran
		if err := dbpkg.SetAgentState(d, "alice", "STALLED", 0, "gone wrong"); err != nil {
			t.Fatal(err)
		}
		tk := p.Tick(cfgOf(p), TickConfig{Cutover: cutover})
		for _, s := range tk.Tick.Passes {
			if s.Name == "notice-digest" {
				if err := s.Run(d, time.Now().Unix()); err != nil {
					t.Fatal(err)
				}
			}
		}
		marked, err := dbpkg.Cursor(d, "stopped_told:fatal:alice")
		if err != nil {
			t.Fatal(err)
		}
		if cutover != (marked != 0) {
			t.Fatalf("cutover=%v: stopped mark %d", cutover, marked)
		}
	}
}

func TestTheStartupBringsUpWaitingWorkAndSettlesAnOldToldSayButNotAnOwedOneBeforeAnyTick(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.db
	say, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "carol", Target: "bob", Body: "old", Kind: "say"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := dbpkg.MarkDelivered(d, say); err != nil { // told by the daemon before
		t.Fatal(err)
	}
	owed, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "carol", Target: "bob", Body: "sent just before the restart", Kind: "say"})
	if err != nil {
		t.Fatal(err)
	}
	tk := p.Tick(cfgOf(p), TickConfig{Terminals: true})
	tk.Startup()
	if row, _ := dbpkg.GetAgentState(d, "alice"); row == nil || row.Terminal.String == "" {
		t.Fatalf("alice has work waiting and no session: startup wakes her in a terminal without waiting for a tick: %+v", row)
	}
	if r, _ := dbpkg.GetRequest(d, say); r == nil || r.State == "queued" {
		t.Fatalf("an old told say is settled at start: %+v", r)
	}
	if r, _ := dbpkg.GetRequest(d, owed); r == nil || r.State != "queued" {
		t.Fatalf("a say not yet told is owed after a restart, not settled: %+v", r)
	}
}

func TestTheTickTypesAnAgentsScheduledMessageWhenItIsDue(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.db
	os.WriteFile(filepath.Join(p.home, "agents", "alice", "agent.yaml"),
		[]byte("vendor: claude\nschedules: '[{\"cron\": \"* * * * *\", \"message\": \"check the queue\"}]'\n"), 0o644)
	tk := p.Tick(cfgOf(p), TickConfig{Cutover: true})
	var run func(q dbpkg.Q, now int64) error
	for _, s := range tk.Tick.Passes {
		if s.Name == "schedules" {
			run = s.Run
		}
	}
	if run == nil {
		t.Fatal("the tick has no schedules pass")
	}
	t0 := time.Now().Unix()
	t0 -= t0 % 60
	if err := run(d, t0); err != nil {
		t.Fatal(err)
	}
	if err := run(d, t0+120); err != nil {
		t.Fatal(err)
	}
	var body string
	if err := d.QueryRow("SELECT body FROM requests WHERE requester = 'schedule' AND target = 'alice'").Scan(&body); err != nil || body != "[schedule * * * * *] check the queue" {
		t.Fatalf("%q %v", body, err)
	}
}
