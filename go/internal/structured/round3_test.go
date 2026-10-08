package structured

import (
	"context"
	"syscall"
	"testing"
	"time"
)

func TestALostEchoFreesTheQueue(t *testing.T) {
	old := EchoDeadline
	EchoDeadline = 100 * time.Millisecond
	t.Cleanup(func() { EchoDeadline = old })
	led := &MemLedger{}
	l := launch(t)
	l.Env = append(l.Env, "FAKE_SILENT=1")
	l.Ledger = led
	t.Setenv("FAKE_SILENT", "1")
	s, _ := openWith(t, l, Handlers{})
	if _, err := s.Send("first", "one", Now); err != nil {
		t.Fatal(err)
	}
	if r, _ := s.Send("second", "two", Queue); r.State != Queued {
		t.Fatalf("the second message is %v, not held", r.State)
	}
	for i := 0; i < 100; i++ {
		if o, _ := led.Get("second"); o.State != Queued {
			return // written, once the first was given up
		}
		time.Sleep(30 * time.Millisecond)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	t.Fatalf("the queue stays held after the lost echo: busy=%v echoing=%d held=%d", s.busy, s.echoing, len(s.held))
}

func TestStartClosesAnExitedSessionAndItsGroupFirst(t *testing.T) {
	m := &Manager{}
	l := launch(t)
	l.Env = append(l.Env, "FAKE_DEAF=1") // leaves a child behind in the CLI's group
	old, err := m.Start(context.Background(), "a", l)
	if err != nil {
		t.Fatal(err)
	}
	pgid := old.Pid()
	syscall.Kill(pgid, syscall.SIGKILL) // only the CLI dies; its child stays
	select {
	case <-old.Done():
	case <-time.After(5 * time.Second):
		t.Fatal("the CLI did not die")
	}
	if err := syscall.Kill(-pgid, 0); err != nil {
		t.Skipf("no child was left behind: %v", err)
	}
	s, err := m.Start(context.Background(), "a", l)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	if err := syscall.Kill(-pgid, 0); err != syscall.ESRCH {
		t.Fatalf("the group of the exited session is still alive after the restart: %v", err)
	}
}

func TestADeniedByTimeoutPromptTakesAnAnswerThatLandedJustBefore(t *testing.T) {
	b, p, _ := broker(t)
	b.Timeout = 50 * time.Millisecond
	var fp string
	beforeLeave = func() {
		fp, _ = openRowFP(p)
		if err := b.Answer("a", fp, true, "", "casey"); err != nil {
			t.Errorf("the answer was refused: %v", err)
		}
	}
	t.Cleanup(func() { beforeLeave = nil })
	d, _ := b.CanUseTool("a")(context.Background(), bash("ls"))
	if !d.Allow {
		t.Fatal("an answer that Answer reported as accepted was not the one used")
	}
}

func openRowFP(p *DBPrompts) (string, error) {
	var fp string
	err := p.DB.QueryRow("SELECT fingerprint FROM prompt_blocks WHERE agent = 'a' AND gone_at IS NULL").Scan(&fp)
	return fp, err
}
