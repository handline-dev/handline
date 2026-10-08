package structured

import (
	"context"
	"syscall"
	"testing"
	"time"
)

func TestKillEndsTheGroupAtOnceAndForgetsTheSession(t *testing.T) {
	old := CloseGrace
	CloseGrace = time.Minute // a Close would wait this long on a CLI that ignores the end of stdin
	t.Cleanup(func() { CloseGrace = old })
	m := &Manager{}
	l := launch(t)
	l.Env = append(l.Env, "FAKE_DEAF=1")
	s, err := m.Start(context.Background(), "a", l)
	if err != nil {
		t.Fatal(err)
	}
	pgid := s.Pid()
	start := time.Now()
	if err := m.Kill("a"); err != nil {
		t.Fatal(err)
	}
	if time.Since(start) > 10*time.Second {
		t.Fatal("kill waited for the grace")
	}
	if err := syscall.Kill(-pgid, 0); err != syscall.ESRCH {
		t.Fatalf("the group is still there: %v", err)
	}
	if m.Get("a") != nil || m.Interrupt("a") == nil {
		t.Fatal("the killed session is still kept")
	}
}
