package structured

import (
	"sync"
	"testing"
	"time"
)

// heldBehindATurn is a session with a turn running (the fake holds "HOLD" until interrupted) and one message held behind it.
func heldBehindATurn(t *testing.T, l Launch) (*Session, *events, Receipt) {
	t.Helper()
	s, ev := openWith(t, l, Handlers{})
	if _, err := s.Send("op-1", "HOLD", Now); err != nil {
		t.Fatal(err)
	}
	if err := s.WaitConfirmed(ctx(t), "op-1"); err != nil {
		t.Fatal(err)
	}
	q, err := s.Send("op-2", "later", Queue)
	if err != nil || q.State != Queued {
		t.Fatalf("not held: %v %v", q, err)
	}
	return s, ev, q
}

func TestAWithdrawnMessageIsNeverWritten(t *testing.T) {
	s, ev, q := heldBehindATurn(t, launch(t))
	r, ok, err := s.Withdraw("op-2")
	if err != nil || !ok || r.State != Withdrawn {
		t.Fatalf("withdraw: %v %v %v", r, ok, err)
	}
	if err := s.WaitConfirmed(ctx(t), "op-2"); err == nil {
		t.Error("a withdrawn message was confirmed")
	}
	s.Interrupt(ctx(t))
	time.Sleep(300 * time.Millisecond)
	if ev.count(isEcho(q.UUID)) != 0 {
		t.Fatal("the withdrawn message reached the CLI")
	}
	if r, ok, _ := s.Withdraw("op-2"); !ok || r.State != Withdrawn {
		t.Errorf("a repeat must answer the same: %v %v", r, ok)
	}
	if r, _ := s.Send("op-2", "later", Queue); r.State != Withdrawn {
		t.Errorf("a retried send of a withdrawn op is %v", r.State)
	}
}

func TestAWrittenMessageIsNotWithdrawn(t *testing.T) {
	s, _ := open(t, Handlers{})
	s.Send("op-1", "hello", Now)
	s.WaitConfirmed(ctx(t), "op-1")
	r, ok, err := s.Withdraw("op-1")
	if err != nil || ok || r.State != Confirmed {
		t.Errorf("withdraw of a confirmed message: %v %v %v", r, ok, err)
	}
	if _, _, err := s.Withdraw("nope"); err == nil {
		t.Error("withdraw of an unknown op did not fail")
	}
}

func TestWithdrawRacingTheReleaseIsConsistent(t *testing.T) {
	s, ev, q := heldBehindATurn(t, launch(t))
	var wg sync.WaitGroup
	var ok bool
	wg.Add(2)
	go func() { defer wg.Done(); s.Interrupt(ctx(t)) }()
	go func() { defer wg.Done(); _, ok, _ = s.Withdraw("op-2") }()
	wg.Wait()
	time.Sleep(300 * time.Millisecond)
	n := ev.count(isEcho(q.UUID))
	if ok && n != 0 {
		t.Fatalf("withdrawn and written (%d echoes)", n)
	}
	if !ok && n != 1 {
		t.Fatalf("not withdrawn and written %d times", n)
	}
}

func TestPromoteWritesAHeldMessageOnceWithPriorityNow(t *testing.T) {
	s, ev, q := heldBehindATurn(t, launch(t))
	r, err := s.Promote("op-2")
	if err != nil || r.State != Sent {
		t.Fatalf("promote: %v %v", r, err)
	}
	if err := s.WaitConfirmed(ctx(t), "op-2"); err != nil {
		t.Fatal(err)
	}
	if again, err := s.Promote("op-2"); err != nil || again.State != Confirmed {
		t.Errorf("a second promote answers the record: %v %v", again, err)
	}
	s.Interrupt(ctx(t))
	time.Sleep(300 * time.Millisecond)
	if n := ev.count(isEcho(q.UUID)); n != 1 {
		t.Fatalf("written %d times", n)
	}
}

func TestPromoteOfAnythingNotHeldWritesNothing(t *testing.T) {
	s, ev, q := heldBehindATurn(t, launch(t))
	s.Withdraw("op-2")
	if r, err := s.Promote("op-2"); err != nil || r.State != Withdrawn {
		t.Errorf("promote of a withdrawn op: %v %v", r, err)
	}
	if r, err := s.Promote("op-1"); err != nil || r.State != Confirmed {
		t.Errorf("promote of a written op: %v %v", r, err)
	}
	if _, err := s.Promote("nope"); err == nil {
		t.Error("promote of an unknown op did not fail")
	}
	s.Interrupt(ctx(t))
	time.Sleep(300 * time.Millisecond)
	if ev.count(isEcho(q.UUID)) != 0 {
		t.Error("a promote of a withdrawn message wrote it")
	}
}

func TestAWithdrawnMessageStaysWithdrawnAfterARestart(t *testing.T) {
	led := &DBLedger{DB: testDB(t), Agent: "backend"}
	l := launch(t)
	l.Ledger = led
	s, _, _ := heldBehindATurn(t, l)
	if _, ok, err := s.Withdraw("op-2"); !ok || err != nil {
		t.Fatal(ok, err)
	}
	s.Close()
	l2 := launch(t)
	l2.Ledger = led
	s2, ev := openWith(t, l2, Handlers{})
	if r, _ := s2.Send("op-2", "later", Queue); r.State != Withdrawn {
		t.Errorf("after a restart: %v", r.State)
	}
	if r, ok, _ := s2.Withdraw("op-2"); !ok || r.State != Withdrawn {
		t.Errorf("withdraw after a restart: %v %v", r, ok)
	}
	if r, _ := s2.Promote("op-2"); r.State != Withdrawn {
		t.Errorf("promote after a restart: %v", r.State)
	}
	time.Sleep(200 * time.Millisecond)
	if ev.count(isEcho(UUIDFor("op-2"))) != 0 {
		t.Error("written after the restart")
	}
}
