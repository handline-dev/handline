package claudecli

import (
	"context"
	"errors"
	"github.com/handline/handline/go/internal/delivery"
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

func TestAMessageTheCLIQueuedAndThenCancelledIsUnknown(t *testing.T) {
	l := launch(t)
	l.Env = append(l.Env, "FAKE_LIFECYCLE_ONLY=1")
	t.Setenv("FAKE_LIFECYCLE_ONLY", "1")
	led := &delivery.MemLedger{}
	l.Ledger = led
	s, _ := openWith(t, l, Handlers{})
	r, _ := s.Send("op-1", "hello", delivery.Now)
	if err := s.WaitConfirmed(ctx(t), "op-1"); err != nil {
		t.Fatal(err)
	}
	s.handle(map[string]any{"type": "command_lifecycle", "command_uuid": r.UUID, "state": "cancelled"})
	if again, _ := s.Send("op-1", "hello", delivery.Now); again.State != delivery.Unknown {
		t.Fatalf("a dropped message is %v, not unknown", again.State)
	}
	if got, _ := led.Get("op-1"); got.State != delivery.Unknown {
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
	if _, err := s.Send("op-1", "hello", delivery.Now); err != nil {
		t.Fatal(err)
	}
	if err := s.WaitConfirmed(ctx(t), "op-1"); err == nil {
		t.Fatal("a message nobody acknowledged was confirmed")
	}
	if r, _ := s.Send("op-1", "hello", delivery.Now); r.State != delivery.Unknown {
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
	led := &delivery.MemLedger{}
	l := launch(t)
	l.Ledger = led
	s, _ := openWith(t, l, Handlers{})
	s.in.Close() // the pipe is gone: the write fails
	if _, err := s.Send("op-1", "hello", delivery.Now); err == nil {
		t.Fatal("a failed write returned a receipt")
	}
	s.mu.Lock()
	n, busy := s.echoing, s.busy
	s.mu.Unlock()
	if n != 0 || busy {
		t.Fatalf("echoing %d busy %v", n, busy)
	}
	if got, _ := led.Get("op-1"); got.State != delivery.Unknown {
		t.Fatalf("ledger: %+v", got)
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
	if _, err := s.Send("op-1", "NEEDPERM", delivery.Now); err != nil {
		t.Fatal(err)
	}
	waitFor(t, assistantText(ev), "tool deny")
}
