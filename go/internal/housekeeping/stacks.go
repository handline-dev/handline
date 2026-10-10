package housekeeping

import (
	"os"
	"os/signal"
	"runtime/pprof"
	"syscall"
)

// DumpStacksOnUSR1 writes every goroutine's stack to stderr (the err log) each time the process gets SIGUSR1: `kill -USR1 <pid>` is how a
// daemon that stopped answering is looked into. The returned function stops it.
func DumpStacksOnUSR1() (stop func()) {
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, syscall.SIGUSR1)
	done := make(chan struct{})
	go func() {
		for {
			select {
			case <-ch:
				pprof.Lookup("goroutine").WriteTo(os.Stderr, 2)
			case <-done:
				return
			}
		}
	}()
	return func() { signal.Stop(ch); close(done) }
}
