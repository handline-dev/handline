package tick

import (
	"os"
	"os/exec"
	"syscall"
	"time"
)

// QuietS is how long the executable must stand unchanged before the daemon restarts onto it: a build or install writes the file over a
// moment, and a restart in the middle would exec half a binary.
const QuietS = 30 * time.Second

// ExeStamp is what a watch compares: the executable's mtime and size, one stat and no read.
type ExeStamp struct {
	ModNs int64
	Size  int64
}

// DevMode is daemon/devmode.py's restart-on-change, watching the running executable instead of the Python source tree: the daemon
// execs itself once the binary it started from has been replaced and the new one starts.
type DevMode struct {
	Enabled func() bool // config.yaml's dev_mode
	Exe     string
	Log     func(string)
	Now     func() time.Time
	// Check proves the new executable starts. Nil runs `Exe --version` with a 30s limit.
	Check func(exe string) error

	base, warned, seen, pending *ExeStamp
	pendingSince                time.Time
}

func stamp(exe string) (ExeStamp, bool) {
	fi, err := os.Stat(exe)
	if err != nil {
		return ExeStamp{}, false // gone between a build's unlink and its rewrite: the next tick's stamp differs from this one's
	}
	return ExeStamp{fi.ModTime().UnixNano(), fi.Size()}, true
}

// Baseline records the executable this process was started from; call it once at startup, before the first tick.
func (d *DevMode) Baseline() {
	if s, ok := stamp(d.Exe); ok {
		d.base = &s
	}
}

func (d *DevMode) check() error {
	if d.Check != nil {
		return d.Check(d.Exe)
	}
	return exec.Command(d.Exe, "--version").Run()
}

// ShouldRestart is called once a tick and says whether to restart onto a changed, started-clean executable.
func (d *DevMode) ShouldRestart() bool {
	if !d.Enabled() {
		d.warned, d.seen, d.pending = nil, nil, nil
		return false
	}
	s, ok := stamp(d.Exe)
	if !ok || d.base == nil {
		return false
	}
	if s == *d.base || (d.warned != nil && s == *d.warned) {
		d.pending = nil
		return false
	}
	if d.pending == nil || *d.pending != s {
		d.pending, d.pendingSince = &s, d.Now()
	}
	if d.Now().Sub(d.pendingSince) < QuietS {
		return false
	}
	if d.seen != nil && *d.seen == s {
		return true
	}
	if err := d.check(); err != nil {
		d.Log("dev-mode: new executable does not start, staying on the running version - " + err.Error())
		d.warned = &s
		return false
	}
	d.Log("dev-mode: executable changed - finishing this tick, then restarting")
	d.seen = &s
	return true
}

// Restart execs the same executable with the same arguments, in this process, after the loop has released the lock and the database. It
// does not return unless the exec fails.
func (d *DevMode) Restart() error {
	return syscall.Exec(d.Exe, os.Args, os.Environ())
}
