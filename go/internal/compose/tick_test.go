package compose

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
)

func TestOneTickWakesAClosedStructuredAgentWithWorkWaitingAndLeavesATerminalAgentAlone(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.Sink.DB
	// bob is a terminal agent with work waiting and no terminal: the Go tick must not touch it, let alone count a failure against it.
	if _, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "human", Target: "bob", Body: "begin", Response: "async", Kind: "ask"}); err != nil {
		t.Fatal(err)
	}
	tk := p.Tick(p.Run.cfg, TickConfig{})
	if _, err := tk.Tick.Run(nil); err != nil {
		t.Fatal(err)
	}
	if !p.Run.Alive("alice") {
		row, _ := dbpkg.GetAgentState(d, "alice")
		t.Fatalf("the tick did not wake alice: %+v", row)
	}
	if !p.Run.WaitIdle("alice", 10*time.Second) {
		t.Fatal("her turn ends")
	}
	if row, _ := dbpkg.GetAgentState(d, "bob"); row != nil && (row.WakeFailures != 0 || row.FailedAttempts != 0 || row.State == "STALLED") {
		t.Fatalf("a terminal agent was touched: %+v", row)
	}
	// The fake CLI leaves no transcript to read a cache clock from, so the answer just given is the activity that keeps the cache warm.
	dbpkg.AddEvent(d, "alice", "Stop")
	if _, err := tk.Tick.Run(nil); err != nil { // a second tick leaves a running agent running
		t.Fatal(err)
	}
	if !p.Run.Alive("alice") {
		t.Fatal("still running")
	}
}

func TestAnAgentWithTheRenewalSwitchOnIsNotStalledForWantingACacheClock(t *testing.T) {
	p, _, _, wt := wired(t)
	d := p.Sink.DB
	os.WriteFile(filepath.Join(p.home, "agents", "alice", "agent.yaml"), []byte("vendor: claude\nstructured: true\ncache_renewal: true\nidle_fold: true\n"), 0o644)
	// A session that is open and idle, with a status naming a transcript that is not there: the clock is unreadable, so no renewal is due.
	if err := dbpkg.SetResumeSession(d, "alice", "s1"); err != nil {
		t.Fatal(err)
	}
	d.Exec("UPDATE agent_state SET closed_at = NULL, state = 'IDLE' WHERE agent = 'alice'")
	_ = wt
	tk := p.Tick(p.Run.cfg, TickConfig{})
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
	tk := p.Tick(p.Run.cfg, TickConfig{Stop: stop})
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

func TestAStructuredAgentWhoseTranscriptShowsAFullContextIsHandedOffByTheTick(t *testing.T) {
	p, lc, _, wt := wired(t)
	d := p.Sink.DB
	for _, args := range [][]string{{"init", "-q"}, {"-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"}} {
		if out, err := exec.Command("git", append([]string{"-C", wt}, args...)...).CombinedOutput(); err != nil {
			t.Skipf("git: %v %s", err, out)
		}
	}
	lc.Handoff.SetDefaultEnabled(false) // the note is asked and answered over turns; this is about the trigger
	if ok, said, err := lc.Manager.WakeAgent("alice", lifecycle.WakeOpts{}); err != nil || !ok {
		t.Fatalf("wake: %v %q %v", ok, said, err)
	}
	if !p.Run.WaitIdle("alice", 10*time.Second) {
		t.Fatal("her turn ends")
	}
	row, _ := dbpkg.GetAgentState(d, "alice")
	acct := p.Run.parts.OpenedOn("alice")
	if acct == "" {
		t.Fatal("the account the session was opened under is not known")
	}
	dir, _ := team.AccountConfigDir(p.home, acct)
	os.MkdirAll(filepath.Join(dir, "projects", "x"), 0o755)
	reply := `{"type":"assistant","isSidechain":false,"message":{"model":"claude-opus-5","usage":{"input_tokens":10,"cache_read_input_tokens":290000,"cache_creation_input_tokens":5000}}}` + "\n"
	os.WriteFile(filepath.Join(dir, "projects", "x", row.ResumeSession.String+".jsonl"), []byte(reply), 0o644)
	dbpkg.AddEvent(d, "alice", "Stop")

	st := tickUsage{p: p, u: lc.usage, roster: lc.roster}.ReadForAgent("alice", acct)
	if st.TrustedCtxUsed == nil || *st.TrustedCtxUsed != 295010 || st.TranscriptPath == "" || st.ModelID != "claude-opus-5" {
		t.Fatalf("%+v", st)
	}
	tk := p.Tick(p.Run.cfg, TickConfig{})
	// A tick that finds the session still busy defers the handoff to the next one, so tick until it is parked.
	for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); time.Sleep(50 * time.Millisecond) {
		if _, err := tk.Tick.Run(nil); err != nil {
			t.Fatal(err)
		}
		if row, _ = dbpkg.GetAgentState(d, "alice"); row.State == "PARKED" {
			break
		}
	}
	if p.Run.Alive("alice") || row.State != "PARKED" || row.ThrottleCount != 0 {
		t.Fatalf("alive %v %+v", p.Run.Alive("alice"), row)
	}
	// The park has run out and nothing else waits: the tick still opens a fresh session and sends it the record.
	for i := 0; i < 3 && !p.Run.Alive("alice"); i++ {
		if _, err := tk.Tick.Run(nil); err != nil {
			t.Fatal(err)
		}
	}
	row, _ = dbpkg.GetAgentState(d, "alice")
	if !p.Run.Alive("alice") || row.ResumeSession.String == "" || row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 {
		t.Fatalf("no fresh session after the park: %+v", row)
	}
	if h, _ := dbpkg.GetHandoff(d, "alice"); h != "" {
		t.Fatalf("the record was not handed over: %q", h)
	}
}

func TestTheStopWaitReasonIsACurrentTaskWithNothingItWaitsOn(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.Sink.DB
	tp := timeboxPorts{p: p, home: p.home}
	if tp.StopWaitReason(d, "alice") != "" {
		t.Fatal("an agent with no current task has no reason to stay")
	}
	tid, err := dbpkg.NewTask(d, "the work", "alice", "boss", 0, dbpkg.DefaultPriority, "report", 0)
	if err != nil {
		t.Fatal(err)
	}
	dbpkg.SetCurrentTask(d, "alice", tid, true)
	if got := tp.StopWaitReason(d, "alice"); got != fmt.Sprintf("T%d is current with no recorded wait", tid) {
		t.Fatalf("%q", got)
	}
	// An open child task is a recorded wait.
	child, _ := dbpkg.NewTask(d, "part", "alice", "alice", tid, dbpkg.DefaultPriority, "report", 0)
	if got := tp.StopWaitReason(d, "alice"); got != "" {
		t.Fatalf("an open child is a wait: %q", got)
	}
	d.Exec("UPDATE tasks SET done_evidence = 'x' WHERE id = ?", child)
	if got := tp.StopWaitReason(d, "alice"); got == "" {
		t.Fatal("a finished child is no wait")
	}
	// A decision the agent opened and the founder has not answered is one; so is a roster proposal waiting on the founder.
	card, _ := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "alice", Target: "human", Body: "which?", Response: "async", Kind: "task"})
	dec, _ := dbpkg.NewTask(d, "which?", "human", "alice", 0, dbpkg.DefaultPriority, "decision", 0)
	dbpkg.LinkRequest(d, dec, card)
	if got := tp.StopWaitReason(d, "alice"); got != "" {
		t.Fatalf("an unanswered decision is a wait: %q", got)
	}
	d.Exec("UPDATE requests SET state = 'done' WHERE id = ?", card)
	os.MkdirAll(filepath.Join(p.home, "hr"), 0o755)
	os.WriteFile(filepath.Join(p.home, "hr", "ab12.yaml"), []byte("id: ab12\nrequested_by: alice\nstate: proposed\n"), 0o644)
	if got := tp.StopWaitReason(d, "alice"); got != "" {
		t.Fatalf("a roster proposal waiting on the founder is a wait: %q", got)
	}
}

func TestTheTickRunsItsPassesWithoutHarmingAnAgent(t *testing.T) {
	p, _, _, _ := wired(t)
	tk := p.Tick(p.Run.cfg, TickConfig{})
	if len(tk.Tick.Passes) != 9 {
		t.Fatalf("passes: %d", len(tk.Tick.Passes))
	}
	var logs []string
	cfg := p.Run.cfg
	cfg.Log = func(format string, a ...any) { logs = append(logs, fmt.Sprintf(format, a...)) }
	tk = p.Tick(cfg, TickConfig{})
	for i := 0; i < 3; i++ {
		if _, err := tk.Tick.Run(nil); err != nil {
			t.Fatal(err)
		}
	}
	for _, l := range logs {
		if strings.Contains(l, "timebox:") || strings.Contains(l, "schedules:") || strings.Contains(l, "notice-digest:") || strings.Contains(l, "task-queue:") || strings.Contains(l, "task-holds:") || strings.Contains(l, "subscriptions:") || strings.Contains(l, "wait-for:") || strings.Contains(l, "apply-settings:") {
			t.Fatalf("a pass failed: %s", l)
		}
	}
}

func TestTheFirstTickAfterMidnightRollsTheDigestOfAStructuredAgent(t *testing.T) {
	p, _, _, _ := wired(t)
	dir := filepath.Join(p.home, "agents", "alice", "journal")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "2026-10-09.md"), []byte("# alice · 2026-10-09\nupdated: 10:00\n\n## In progress\n- the port\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 10, 9, 23, 59, 0, 0, time.Local)
	cfg := p.Run.cfg
	cfg.Now = func() time.Time { return now }
	tk := p.Tick(cfg, TickConfig{})
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
	cfg := p.Run.cfg
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
	tk := p.Tick(p.Run.cfg, TickConfig{})
	got, err := tk.Tick.Sweeps.RelinkAccounts()
	if err != nil || len(got["a1"]) == 0 {
		t.Fatalf("%v %v", got, err)
	}
	if _, err := os.Lstat(filepath.Join(cdir, "CLAUDE.md")); err != nil {
		t.Fatal("not linked")
	}
}

func TestTheLoopRunsTheDescriptorCheckLogRotationAndScratchSweepAfterEachTick(t *testing.T) {
	p, _, _, _ := wired(t)
	var mu sync.Mutex
	var logged []string
	cfg := p.Run.cfg
	cfg.Log = func(f string, a ...any) { mu.Lock(); logged = append(logged, fmt.Sprintf(f, a...)); mu.Unlock() }
	log := filepath.Join(p.home, "logs", "daemon.err.log")
	os.MkdirAll(filepath.Dir(log), 0o755)
	os.WriteFile(log, make([]byte, 11<<20), 0o644)
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "old", "node_modules"), 0o755)
	os.WriteFile(filepath.Join(root, "old", "AGENTS.md"), nil, 0o644)
	longAgo := time.Now().Add(-48 * time.Hour)
	for _, f := range []string{"node_modules", "AGENTS.md", ""} {
		os.Chtimes(filepath.Join(root, "old", f), longAgo, longAgo)
	}
	tk := p.Tick(cfg, TickConfig{Stop: make(chan struct{}), ScratchRoot: root})
	tk.Loop.MaxTicks = 1
	<-tk.Start()
	if st, _ := os.Stat(log); st.Size() != 0 {
		t.Fatal("the log over its limit was not rotated")
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := os.Stat(filepath.Join(root, "old")); err != nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the stale copy was not swept")
		}
		time.Sleep(10 * time.Millisecond)
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
	tk := p.Tick(p.Run.cfg, TickConfig{Cutover: cutover})
	p.Sink.DB.Close()
	for _, s := range tk.Tick.Passes {
		if s.Name == name {
			defer func() {
				if recover() != nil {
					ran = true
				}
			}()
			return s.Run(p.Sink.DB, time.Now().Unix()) != nil
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
		tk := p.Tick(p.Run.cfg, TickConfig{Cutover: cutover})
		for _, s := range tk.Tick.Passes {
			if s.Name == "subscriptions" {
				if err := s.Run(p.Sink.DB, time.Now().Unix()); err != nil {
					t.Fatal(err)
				}
			}
		}
		var n int
		p.Sink.DB.QueryRow("SELECT COUNT(*) FROM cursors WHERE name LIKE 'plan_checked:%'").Scan(&n)
		if (n > 0) != cutover {
			t.Fatalf("cutover=%v: %d plan cursors", cutover, n)
		}
	}
}

func TestTheTickRunsTheNoticeSweepAsALevelOfPersistedState(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.Sink.DB
	key := "stopped_told:fatal:alice"
	tick := func() {
		t.Helper()
		if _, err := p.Tick(p.Run.cfg, TickConfig{Cutover: true}).Tick.Run(nil); err != nil { // a new tick each time: nothing is remembered in memory
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

func TestTheNoticeDigestIsSweptByTheGoTickOnlyFromTheCutover(t *testing.T) {
	for _, cutover := range []bool{false, true} {
		p, _, _, _ := wired(t)
		d := p.Sink.DB
		t.Cleanup(func() { p.Run.WaitIdle("alice", 10*time.Second) }) // the telling may wake her in the background
		if _, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: dbpkg.NoticeRequester, Target: "alice", Body: "T3 done by bob: ok", Kind: "say", Notice: true}); err != nil {
			t.Fatal(err)
		}
		tk := p.Tick(p.Run.cfg, TickConfig{Cutover: cutover})
		for _, s := range tk.Tick.Passes {
			if s.Name == "notice-digest" {
				if err := s.Run(d, time.Now().Unix()+86400); err != nil {
					t.Fatal(err)
				}
			}
		}
		left, err := dbpkg.PendingNotices(d, "alice")
		if err != nil {
			t.Fatal(err)
		}
		if cutover == (len(left) != 0) {
			t.Fatalf("cutover=%v: %d notices left waiting", cutover, len(left))
		}
	}
}

func TestTheStartupBringsUpWaitingWorkAndSettlesAnOldSayBeforeAnyTick(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.Sink.DB
	say, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "carol", Target: "bob", Body: "old", Response: "none", Kind: "say"})
	if err != nil {
		t.Fatal(err)
	}
	tk := p.Tick(p.Run.cfg, TickConfig{})
	tk.Startup()
	if !p.Run.Alive("alice") {
		t.Fatal("alice has work waiting and no session: startup wakes her without waiting for a tick")
	}
	if r, _ := dbpkg.GetRequest(d, say); r == nil || r.State == "queued" {
		t.Fatalf("an old say is settled at start: %+v", r)
	}
	p.Run.WaitIdle("alice", 10*time.Second)
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

func TestTheTickTypesAnAgentsScheduledMessageWhenItIsDue(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.Sink.DB
	os.WriteFile(filepath.Join(p.home, "agents", "alice", "agent.yaml"),
		[]byte("vendor: claude\nstructured: true\nschedules: '[{\"cron\": \"* * * * *\", \"message\": \"check the queue\"}]'\n"), 0o644)
	tk := p.Tick(p.Run.cfg, TickConfig{})
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
	for deadline := time.Now().Add(10 * time.Second); !p.Run.Alive("alice") && time.Now().Before(deadline); { // the telling wakes her in the background
		time.Sleep(20 * time.Millisecond)
	}
	p.Run.WaitIdle("alice", 10*time.Second)
}
