package structured

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

func TestAFailedCloseIsNotReportedAsDoneByTheNextCall(t *testing.T) {
	s, _ := open(t, Handlers{})
	left := errors.New("processes of the claude group are still running")
	proveGone = func(int) error { return left }
	t.Cleanup(func() { proveGone = groupGone })
	if err := s.Close(); err == nil {
		t.Fatal("a group that is not gone was reported gone")
	}
	if err := s.Close(); err == nil {
		t.Fatal("the second Close forgot the first one failed")
	}
	proveGone = groupGone
	if err := s.Close(); err != nil {
		t.Fatalf("once the group is gone Close says so: %v", err)
	}
}

func TestTwoStartsForOneAgentRunOneCLI(t *testing.T) {
	m := &Manager{}
	l := launch(t)
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := m.Start(context.Background(), "a", l)
			errs <- err
		}()
	}
	wg.Wait()
	close(errs)
	ok := 0
	for err := range errs {
		if err == nil {
			ok++
		}
	}
	t.Cleanup(func() { m.Stop("a") })
	if ok != 1 {
		t.Fatalf("%d Starts succeeded for one agent", ok)
	}
	// An exited session is replaced, and its group proven gone first.
	old := m.Get("a")
	old.Close()
	if _, err := m.Start(context.Background(), "a", l); err != nil {
		t.Fatalf("restart after exit: %v", err)
	}
	if m.Get("a") == old {
		t.Fatal("the exited session was kept")
	}
}

func TestAMessageTheCLIQueuedAndThenCancelledIsUnknown(t *testing.T) {
	l := launch(t)
	l.Env = append(l.Env, "FAKE_LIFECYCLE_ONLY=1")
	t.Setenv("FAKE_LIFECYCLE_ONLY", "1")
	led := &MemLedger{}
	l.Ledger = led
	s, _ := openWith(t, l, Handlers{})
	r, _ := s.Send("op-1", "hello", Now)
	if err := s.WaitConfirmed(ctx(t), "op-1"); err != nil {
		t.Fatal(err)
	}
	s.handle(map[string]any{"type": "command_lifecycle", "command_uuid": r.UUID, "state": "cancelled"})
	if again, _ := s.Send("op-1", "hello", Now); again.State != Unknown {
		t.Fatalf("a dropped message is %v, not unknown", again.State)
	}
	if got, _ := led.Get("op-1"); got.State != Unknown {
		t.Fatalf("ledger: %+v", got)
	}
}

func TestALostEchoBecomesUnknownAndFreesTheCounts(t *testing.T) {
	old := EchoDeadline
	EchoDeadline = 100 * time.Millisecond
	t.Cleanup(func() { EchoDeadline = old })
	l := launch(t)
	l.Env = append(l.Env, "FAKE_SILENT=1")
	t.Setenv("FAKE_SILENT", "1")
	s, _ := openWith(t, l, Handlers{})
	if _, err := s.Send("op-1", "hello", Now); err != nil {
		t.Fatal(err)
	}
	if err := s.WaitConfirmed(ctx(t), "op-1"); err == nil {
		t.Fatal("a message nobody acknowledged was confirmed")
	}
	if r, _ := s.Send("op-1", "hello", Now); r.State != Unknown {
		t.Fatalf("%v", r.State)
	}
	s.mu.Lock()
	n := s.echoing
	s.mu.Unlock()
	if n != 0 {
		t.Fatalf("echoing stays %d", n)
	}
}

func TestAWriteErrorGivesBackTheCountsAndLeavesTheMessageUnknown(t *testing.T) {
	led := &MemLedger{}
	l := launch(t)
	l.Ledger = led
	s, _ := openWith(t, l, Handlers{})
	s.in.Close() // the pipe is gone: the write fails
	if _, err := s.Send("op-1", "hello", Now); err == nil {
		t.Fatal("a failed write returned a receipt")
	}
	s.mu.Lock()
	n, busy := s.echoing, s.busy
	s.mu.Unlock()
	if n != 0 || busy {
		t.Fatalf("echoing %d busy %v", n, busy)
	}
	if got, _ := led.Get("op-1"); got.State != Unknown {
		t.Fatalf("ledger: %+v", got)
	}
}

func TestAnAnswerAfterTheTimeoutIsRefusedAndReportsNothing(t *testing.T) {
	b, _, ev := broker(t)
	b.Timeout = 100 * time.Millisecond
	done := make(chan Decision, 1)
	go func() { d, _ := b.CanUseTool("a")(context.Background(), bash("ls")); done <- d }()
	fp := bash("ls").ToolUse // the prompt's id is its call's: no poll for a row that a short timeout may close before it is seen
	if d := <-done; d.Allow {
		t.Fatalf("%+v", d)
	}
	if err := b.Answer("a", fp, true, "", "casey"); !errors.Is(err, ErrNoPrompt) {
		t.Fatalf("an answer to a prompt that already timed out: %v", err)
	}
	for _, e := range ev.snapshot() {
		if e.Type == "permission_answer" && e.Data["allow"] == true {
			t.Fatal("an allow was reported for a prompt that was denied")
		}
	}
}

func TestAHandlerThatIgnoresItsContextIsDeniedAtTheTimeout(t *testing.T) {
	old := PromptTimeout
	PromptTimeout = 100 * time.Millisecond
	t.Cleanup(func() { PromptTimeout = old })
	stuck := make(chan struct{})
	t.Cleanup(func() { close(stuck) })
	s, ev := open(t, Handlers{CanUseTool: func(context.Context, ToolRequest) (Decision, error) {
		<-stuck
		return Decision{Allow: true}, nil
	}})
	if _, err := s.Send("op-1", "NEEDPERM", Now); err != nil {
		t.Fatal(err)
	}
	waitFor(t, assistantText(ev), "tool deny")
}
