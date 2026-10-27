package housekeeping

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/db"
)

const (
	// OrphansKey is the config.yaml key, and the modes it takes: dry (the default) only logs and records what it would stop, on stops it, off does nothing.
	OrphansKey = "reap_orphans"
	OrphansDry = "dry"
	OrphansOn  = "on"
	OrphansOff = "off"
	// OrphanEvery is how often the sweep looks, and OrphanMinAge how long a process must have been an orphan of init to be one of ours left behind.
	OrphanEvery  = int64(600)
	OrphanMinAge = 30 * time.Minute
)

// OrphansModeOf is the configured mode; anything but on and off reads as dry, so nothing is stopped until the founder turns it on.
func OrphansModeOf(cfg map[string]any) string {
	switch s, _ := cfg[OrphansKey].(string); s {
	case OrphansOn, OrphansOff:
		return s
	}
	return OrphansDry
}

// Proc is one running process: its parent, how long it has run, and its whole command line.
type Proc struct {
	PID, PPID int
	Age       time.Duration
	Command   string
}

// ours are the commands the agents' work leaves behind when its parent dies; each is stopped only with the folder it works in under a worktree.
var ours = []struct {
	kind string
	re   *regexp.Regexp
}{
	{"yes", regexp.MustCompile(`^(?:\S*/)?yes(?:\s|$)`)},
	{"headless Chrome", regexp.MustCompile(`--headless\b.*--user-data-dir=|--user-data-dir=.*--headless\b`)},
	{"vitest", regexp.MustCompile(`\bvitest\b`)},
	{"vite", regexp.MustCompile(`(?:^|[/\s])vite(?:\s|$)`)},
	{"go test binary", regexp.MustCompile(`^\S*/go-build[^\s]*\.test(?:\s|$)`)},
	{"Electron", regexp.MustCompile(`Electron\.app/Contents/MacOS/Electron|(?:^|/)electron(?:\s|$)`)},
}

var userDataDir = regexp.MustCompile(`--user-data-dir=(\S+)`)

// underWorktrees is whether a path lies in a folder named <repo>.worktrees or <repo>.worktrees.noindex.
func underWorktrees(path string) bool {
	for _, part := range strings.Split(filepath.Clean(path), string(filepath.Separator)) {
		if strings.HasSuffix(part, ".worktrees") || strings.HasSuffix(part, ".worktrees.noindex") {
			return true
		}
	}
	return false
}

func under(path, root string) bool {
	return root != "" && (path == root || strings.HasPrefix(path, root+string(filepath.Separator)))
}

// looksOurs is whether the command line is one of the commands above: the cheap test made before a process's folder is looked up.
func looksOurs(command string) bool {
	for _, o := range ours {
		if o.re.MatchString(command) {
			return true
		}
	}
	return false
}

// Ours is what kind of left-behind work p is, "" for anything else: an orphan of init (ppid 1) of OrphanMinAge or more, one of the commands
// above, and working in a folder under a worktree (cwd, "" when unknown). Headless Chrome also counts when its --user-data-dir is under a
// worktree or the scratch root. Nothing else is ever ours.
func Ours(p Proc, cwd, scratch string) string {
	if p.PID <= 1 || p.PPID != 1 || p.Age < OrphanMinAge {
		return ""
	}
	for _, o := range ours {
		if !o.re.MatchString(p.Command) {
			continue
		}
		if cwd != "" && underWorktrees(cwd) {
			return o.kind
		}
		if o.kind == "headless Chrome" {
			if m := userDataDir.FindStringSubmatch(p.Command); m != nil && (underWorktrees(m[1]) || under(m[1], scratch)) {
				return o.kind
			}
		}
		return ""
	}
	return ""
}

// Orphans stops what the agents' work left running after its parent died. It reads the process list every OrphanEvery seconds.
type Orphans struct {
	Mode    func() string
	Log     func(string)
	Scratch string
	// List, Cwd and Stop are the machine: nil run ps, lsof and a SIGTERM followed by SIGKILL.
	List func() ([]Proc, error)
	Cwd  func(pid int) string
	Stop func(pid int) error

	checked int64
}

// Check runs the sweep at most every OrphanEvery seconds. Each process stopped, or in dry mode each that would be, is an event.
func (o *Orphans) Check(q db.Q, now int64) error {
	mode := o.Mode()
	if mode == OrphansOff || (o.checked != 0 && now-o.checked < OrphanEvery) {
		return nil
	}
	o.checked = now
	list := o.List
	if list == nil {
		list = psList
	}
	procs, err := list()
	if err != nil {
		return err
	}
	cwdOf, stop := o.Cwd, o.Stop
	if cwdOf == nil {
		cwdOf = lsofCwd
	}
	if stop == nil {
		stop = terminate
	}
	for _, p := range procs {
		if p.PID == os.Getpid() || p.PID == os.Getppid() || p.PPID != 1 || p.Age < OrphanMinAge || !looksOurs(p.Command) {
			continue
		}
		cwd := cwdOf(p.PID)
		kind := Ours(p, cwd, o.Scratch)
		if kind == "" {
			continue
		}
		event, verb := "orphan_would_stop", "would stop"
		if mode == OrphansOn {
			if err := stop(p.PID); err != nil {
				o.Log(fmt.Sprintf("orphans: %s %d could not be stopped: %v", kind, p.PID, err))
				continue
			}
			event, verb = "orphan_stopped", "stopped"
		}
		cmd := p.Command
		if len(cmd) > 200 {
			cmd = cmd[:200]
		}
		if _, err := db.AddEvent(q, "", event, db.KV{K: "pid", V: p.PID}, db.KV{K: "kind", V: kind}, db.KV{K: "age_s", V: int64(p.Age.Seconds())},
			db.KV{K: "cwd", V: cwd}, db.KV{K: "command", V: cmd}); err != nil {
			return err
		}
		o.Log(fmt.Sprintf("orphans: %s %s %d (%s, up %dm, in %s)", verb, kind, p.PID, filepath.Base(strings.Fields(cmd + " x")[0]), int(p.Age.Minutes()), cwd))
	}
	return nil
}

// psList is every process: pid, ppid, elapsed time and command line.
func psList() ([]Proc, error) {
	out, err := exec.Command("ps", "-Ao", "pid=,ppid=,etime=,command=").Output()
	if err != nil {
		return nil, err
	}
	return parsePS(string(out)), nil
}

func parsePS(out string) []Proc {
	var procs []Proc
	for _, line := range strings.Split(out, "\n") {
		f := strings.Fields(line)
		if len(f) < 4 {
			continue
		}
		pid, e1 := strconv.Atoi(f[0])
		ppid, e2 := strconv.Atoi(f[1])
		age, ok := parseEtime(f[2])
		if e1 != nil || e2 != nil || !ok {
			continue
		}
		procs = append(procs, Proc{PID: pid, PPID: ppid, Age: age, Command: strings.Join(f[3:], " ")})
	}
	return procs
}

// parseEtime reads ps's elapsed time: [[dd-]hh:]mm:ss.
func parseEtime(s string) (time.Duration, bool) {
	var days int
	if d, rest, ok := strings.Cut(s, "-"); ok {
		n, err := strconv.Atoi(d)
		if err != nil {
			return 0, false
		}
		days, s = n, rest
	}
	parts := strings.Split(s, ":")
	if len(parts) < 2 || len(parts) > 3 {
		return 0, false
	}
	secs := 0
	for _, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil {
			return 0, false
		}
		secs = secs*60 + n
	}
	return time.Duration(days)*24*time.Hour + time.Duration(secs)*time.Second, true
}

// lsofCwd is the working directory of a process, "" when it cannot be read.
func lsofCwd(pid int) string {
	out, err := exec.Command("lsof", "-a", "-d", "cwd", "-Fn", "-p", strconv.Itoa(pid)).Output()
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(out), "\n") {
		if strings.HasPrefix(line, "n") {
			return line[1:]
		}
	}
	return ""
}

// terminate asks a process to end and kills it if it is still there a moment later.
func terminate(pid int) error {
	if err := syscall.Kill(pid, syscall.SIGTERM); err != nil {
		return err
	}
	time.Sleep(2 * time.Second)
	if syscall.Kill(pid, 0) == nil {
		return syscall.Kill(pid, syscall.SIGKILL)
	}
	return nil
}
