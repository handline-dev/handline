package claudecli

import (
	"github.com/handline/handline/go/internal/delivery"
	"testing"
	"time"
)

func TestALostEchoFreesTheQueue(t *testing.T) {
	old := EchoDeadline
	EchoDeadline = 100 * time.Millisecond
	t.Cleanup(func() { EchoDeadline = old })
	led := &delivery.MemLedger{}
	l := launch(t)
	l.Env = append(l.Env, "FAKE_SILENT=1")
	l.Ledger = led
	t.Setenv("FAKE_SILENT", "1")
	s, _ := openWith(t, l, Handlers{})
	if _, err := s.Send("first", "one", delivery.Now); err != nil {
		t.Fatal(err)
	}
	if r, _ := s.Send("second", "two", delivery.Queue); r.State != delivery.Queued {
		t.Fatalf("the second message is %v, not held", r.State)
	}
	for i := 0; i < 100; i++ {
		if o, _ := led.Get("second"); o.State != delivery.Queued {
			return // written, once the first was given up
		}
		time.Sleep(30 * time.Millisecond)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	t.Fatalf("the queue stays held after the lost echo: busy=%v echoing=%d held=%d", s.busy, s.echoing, len(s.held))
}
