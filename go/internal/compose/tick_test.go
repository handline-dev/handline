package compose

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
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
	os.WriteFile(filepath.Join(p.home, "agents", "alice", "agent.yaml"), []byte("vendor: claude\nmode: structured\ncache_renewal: true\nidle_fold: true\n"), 0o644)
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

// overContext reads every agent as over its context limit, so the handoff trigger fires.
type overContext struct{ tick.Usage }

func (overContext) ReadForAgent(string, string) tick.Status {
	over := 250_000
	return tick.Status{SessionID: "s1", TrustedCtxUsed: &over}
}

func TestAHandoffThisDaemonCannotPerformIsSaidOnceAndNeverCountedAgainstTheAgent(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.Sink.DB
	var logs []string
	cfg := p.Run.cfg
	cfg.Log = func(format string, a ...any) { logs = append(logs, fmt.Sprintf(format, a...)) }
	if _, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "human", Target: "alice", Body: "begin", Response: "async", Kind: "ask"}); err != nil {
		t.Fatal(err)
	}
	tk := p.Tick(cfg, TickConfig{})
	if _, err := tk.Tick.Run(nil); err != nil { // wakes alice
		t.Fatal(err)
	}
	p.Run.WaitIdle("alice", 10*time.Second)
	tk.Tick.Usage = overContext{tk.Tick.Usage}
	logs = nil
	for i := 0; i < 3; i++ {
		res, err := tk.Tick.Run(nil)
		if err != nil {
			t.Fatal(err)
		}
		for _, a := range res.Actions {
			if strings.Contains(a, "handoff") {
				t.Fatalf("a handoff nothing tried was reported: %v", res.Actions)
			}
		}
	}
	n := 0
	for _, l := range logs {
		if strings.Contains(l, "a handoff is due (context) but performing a handoff is not served by this daemon yet") {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("said %d times: %v", n, logs)
	}
	row, _ := dbpkg.GetAgentState(d, "alice")
	if row.ThrottleCount != 0 || row.State == "STALLED" {
		t.Fatalf("an untried handoff counted against the agent: %+v", row)
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
	if len(tk.Tick.Passes) != 3 {
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
		if strings.Contains(l, "timebox:") || strings.Contains(l, "wait-for:") || strings.Contains(l, "apply-settings:") {
			t.Fatalf("a pass failed: %s", l)
		}
	}
}
