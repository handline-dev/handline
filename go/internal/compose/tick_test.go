package compose

import (
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
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
	if _, err := tk.Tick.Run(nil); err != nil {
		t.Fatal(err)
	}
	row, _ = dbpkg.GetAgentState(d, "alice")
	if p.Run.Alive("alice") || row.State != "PARKED" || row.ThrottleCount != 0 {
		t.Fatalf("alive %v %+v", p.Run.Alive("alice"), row)
	}
}
