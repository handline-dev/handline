// Package lifecycle is the Python daemon's session lifecycle: the single-instance hold (daemon/locks.py), the idle-close settings
// (session/idle_close.py), and starting, parking, waking and recovering sessions (daemon/lifecycle.py, reconciler.py, session/launch.py,
// team/spawn.py, usage/recover.py). What it does not own it reaches through the small interfaces in ports.go; tests use fakes.
package lifecycle

import (
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/session"
)

// DaemonLock is the name of the hold that makes this a single-instance program: one per home, so two installations on one machine
// still supervise their own.
const DaemonLock = "daemon"

// Processes is the process table as the daemon lookup reads it.
type Processes interface {
	// Table is every process's pid and command line; ok is false when it could not be read.
	Table() (rows []session.Process, ok bool)
	// ProcessEnv is pid's own environment; ok is false when none came back.
	ProcessEnv(pid int) (string, bool)
}

// Locks finds the daemon supervising a home.
type Locks struct {
	Home  string
	Procs Processes
	PID   int // this process; os.Getpid when 0
}

func (l *Locks) pid() int {
	if l.PID != 0 {
		return l.PID
	}
	return os.Getpid()
}

// RunningElsewhere is the pid of the daemon supervising this home, 0 and false when none is. Asked of the lock that refuses a second
// daemon, so a daemon is reported exactly when it holds it. One that holds the lock without having written its pid is named from the
// process table instead; only one that is in neither reads as none.
func (l *Locks) RunningElsewhere() (pid int, ok bool) {
	holder, held := lock.Holder(l.Home, DaemonLock)
	if !held {
		return 0, false
	}
	if holder != 0 {
		return holder, true
	}
	if found := l.ProcessList(); len(found) > 0 {
		return found[0], true
	}
	return 0, false
}

// supervisesThisHome is whether that daemon is ours, or another installation's. Compared as directories, not as spellings:
// /tmp and /private/tmp are one place on macOS.
func (l *Locks) supervisesThisHome(pid int) bool {
	env, ok := l.Procs.ProcessEnv(pid)
	if !ok {
		// Unreadable is the one worth naming: a supervisor we cannot place is exactly the one silence let run twice.
		return true
	}
	// Read, but not set: a daemon started by hand usually has no OCP_HOME, and that process still runs against the default home.
	home := ""
	for _, tok := range strings.Fields(env) {
		if strings.HasPrefix(tok, "OCP_HOME=") || strings.HasPrefix(tok, "HANDLINE_HOME=") {
			home = tok[strings.Index(tok, "=")+1:]
			break
		}
	}
	if home == "" {
		home = defaultHome()
	} else {
		home = expandUser(home)
	}
	return realpath(home) == realpath(l.Home)
}

func defaultHome() string {
	user := paths.UserHome()
	if st, err := os.Stat(filepath.Join(user, ".handline")); err == nil && st.IsDir() {
		return filepath.Join(user, ".handline")
	}
	return filepath.Join(user, ".orcapipe")
}

func expandUser(p string) string {
	if p == "~" {
		return paths.UserHome()
	}
	if strings.HasPrefix(p, "~/") {
		return filepath.Join(paths.UserHome(), p[2:])
	}
	return p
}

// realpath resolves symlinks as far as the path exists, like os.path.realpath.
func realpath(p string) string {
	abs, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	rest := ""
	for cur := abs; ; cur = filepath.Dir(cur) {
		if r, err := filepath.EvalSymlinks(cur); err == nil {
			return filepath.Join(r, rest)
		}
		if cur == filepath.Dir(cur) {
			return abs
		}
		rest = filepath.Join(filepath.Base(cur), rest)
	}
}

// ProcessList is the pids of every `ocp daemon` supervising this home, this one aside. The lock refuses the next duplicate; it cannot
// end one that was already running when it arrived, and the lock names only its holder. The process table is the only place both appear.
func (l *Locks) ProcessList() []int {
	rows, _ := l.Procs.Table()
	var found []int
	for _, r := range rows {
		argv := strings.Fields(r.Command)
		// The program being run: our own path, `daemon` next, and both at the front of the command line. Anywhere else in it is somebody
		// talking about us - a `grep -n hl daemon`, an editor on this file, a session whose system prompt quotes the command.
		match := false
		for i := 0; i < len(argv)-1 && i < 2; i++ {
			if strings.HasSuffix(argv[i], "/ocp") && argv[i+1] == "daemon" {
				match = true
				break
			}
		}
		if match && r.PID != l.pid() && l.supervisesThisHome(r.PID) {
			found = append(found, r.PID)
		}
	}
	return found
}

// writeAtomic replaces path in one step, so a reader sees the old file or the new.
func writeAtomic(path, text string) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	if _, err := tmp.WriteString(text); err != nil {
		tmp.Close()
		os.Remove(tmp.Name())
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	return nil
}
