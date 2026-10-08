package tick

import (
	"fmt"
	"time"
)

// Loop is daemon/loop.py's tick loop. The lock, database, socket and signal handling around it belong to whoever builds the daemon: it
// is handed Stop, closed on SIGINT/SIGTERM, and returns once the tick in hand has finished.
type Loop struct {
	Tick *Tick
	Log  func(string)
	Dev  *DevMode // nil: no dev mode
	Stop <-chan struct{}
	// Signalled reports whether Stop was a real signal, which wins over a restart still being decided.
	Signalled func() bool
	// Housekeeping runs after each tick (fd check, log rotation); one that fails is logged and does not stop the daemon.
	Housekeeping []NamedSweep
	// MaxTicks counts this process's own ticks, 0 for no limit: a restart starts the count over.
	MaxTicks int
	Sleep    func(time.Duration) bool // false when Stop came first; nil waits on Stop
}

func (l *Loop) sleep(d time.Duration) bool {
	if l.Sleep != nil {
		return l.Sleep(d)
	}
	select {
	case <-l.Stop:
		return false
	case <-time.After(d):
		return true
	}
}

func (l *Loop) stopped() bool {
	select {
	case <-l.Stop:
		return true
	default:
		return false
	}
}

// Run ticks until stopped and reports whether the daemon should now restart onto a changed executable.
func (l *Loop) Run(samples map[string][]Sample) (restart bool) {
	ticks := 0
	for !l.stopped() {
		if _, err := l.Tick.Run(samples); err != nil {
			l.Log(fmt.Sprintf("tick failed: %v", err))
		}
		ticks++
		for _, h := range l.Housekeeping {
			if err := h.Run(l.Tick.Q, l.Tick.Now()); err != nil {
				l.Log(fmt.Sprintf("%s failed: %v", h.Name, err))
			}
		}
		if l.Dev != nil && l.Dev.ShouldRestart() {
			if l.Signalled != nil && l.Signalled() {
				l.Log("dev-mode: restart skipped - a stop signal arrived during the check")
			} else {
				return true
			}
		}
		if l.MaxTicks > 0 && ticks >= l.MaxTicks {
			break
		}
		if !l.sleep(PollS * time.Second) {
			break
		}
	}
	l.Log(fmt.Sprintf("stopped after %d tick(s)", ticks))
	return false
}
