package housekeeping

import (
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"runtime/pprof"
	"syscall"
	"time"
)

// ProfileFor is how long a CPU profile ProfileCPUOnUSR2 starts runs.
const ProfileFor = 30 * time.Second

// ProfileCPUOnUSR2 writes a CPU profile of the next ProfileFor to dir/cpu-<time>.pprof each time the process gets SIGUSR2, and logs
// the path when it is written: `kill -USR2 <pid>`, then `go tool pprof -top <file>`, is how a daemon that runs hot is looked into. One
// signal while a profile runs starts the next profile after it; more are dropped. The returned function stops it, ending a running
// profile early.
func ProfileCPUOnUSR2(dir string, log func(string)) (stop func()) {
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, syscall.SIGUSR2)
	done, ended := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(ended)
		for {
			select {
			case <-ch:
				log(profileCPU(dir, ProfileFor, done))
			case <-done:
				return
			}
		}
	}()
	// A profile cut short is written and said before stop returns, so a stopping daemon does not leave an empty file.
	return func() { signal.Stop(ch); close(done); <-ended }
}

// profileCPU profiles the CPU for d, or until done closes, into a new file under dir, and says where it went or why it did not.
func profileCPU(dir string, d time.Duration, done <-chan struct{}) string {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "cpu profile: " + err.Error()
	}
	path := filepath.Join(dir, "cpu-"+time.Now().Format("20060102-150405")+".pprof")
	f, err := os.Create(path)
	if err != nil {
		return "cpu profile: " + err.Error()
	}
	defer f.Close()
	if err := pprof.StartCPUProfile(f); err != nil {
		return "cpu profile: " + err.Error()
	}
	select {
	case <-time.After(d):
	case <-done:
	}
	pprof.StopCPUProfile()
	return fmt.Sprintf("cpu profile of %s written to %s", d, path)
}
