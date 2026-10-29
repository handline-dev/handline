// heavyslot runs a command once it holds one of the machine's heavy-run slots: heavyslot -- <command> [args...]
//
// A slot is an flock on <dir>/slot-<n>.lock (dir: HANDLINE_HEAVY_DIR, else ~/.handline/heavy-slots; n up to HANDLINE_HEAVY_SLOTS, default
// 2), freed by the kernel when its holder dies; the holder writes who it is into the file. Waiters go in the order they came (a ticket
// each). The command runs with HANDLINE_HEAVY_SLOT set, so a heavy script it starts shares the slot. With no writable directory it runs anyway.
package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/heavyslots"
	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/loadgate"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sysres"
	"github.com/handline/handline/go/internal/usage"
)

var poll = 2 * time.Second

// freeMemory is the machine's free and total memory; a test stubs it.
var freeMemory = func() (free, total int64, err error) {
	m, err := sysres.Read()
	return m.RAMFree, m.RAMTotal, err
}

// lowMemory is why a free slot is not taken now, "" when free memory is at least ram_free_min_pct (the load gate's key and default) or
// cannot be read: a run is never held on a reading it did not get.
func lowMemory() string {
	limit := loadgate.LimitsOf(usage.LoadConfig(paths.Home())).RAMFreePct
	free, total, err := freeMemory()
	if limit <= 0 || err != nil || total <= 0 {
		return ""
	}
	if pct := int(free * 100 / total); pct < limit {
		return fmt.Sprintf("free memory %d%% under %d%%", pct, limit)
	}
	return ""
}

// freeDisk is the free and total bytes of the volume holding path; a test stubs it.
var freeDisk = sysres.StatfsFree

const gb = int64(1) << 30

// lowDisk is why a free slot is not taken now, "" when the home's volume has at least disk_free_min_gb free (0 is off) or cannot be read:
// a run is never held on a reading it did not get.
func lowDisk() string {
	limit := loadgate.LimitsOf(usage.LoadConfig(paths.Home())).DiskFreeGB
	if limit <= 0 {
		return ""
	}
	free, _, err := freeDisk(paths.Home())
	if err != nil {
		fmt.Fprintf(os.Stderr, "heavyslot: free disk not read: %v\n", err)
		return ""
	}
	if free < int64(limit)*gb {
		return fmt.Sprintf("free disk %d GB under %d GB", free/gb, limit)
	}
	return ""
}

func main() {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "--" {
		args = args[1:]
	}
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: heavyslot -- <command> [args...]")
		os.Exit(2)
	}
	if os.Getenv("HANDLINE_HEAVY_SLOT") == "" {
		class := heavyClass
		if isLight(args) {
			class = lightClass
		}
		if lock, n, err := acquireClass(class); err != nil {
			fmt.Fprintf(os.Stderr, "heavyslot: runs without a slot: %v\n", err)
		} else {
			defer lock.Close()
			os.Setenv("HANDLINE_HEAVY_SLOT", strconv.Itoa(n))
		}
	}
	os.Exit(run(args))
}

// A class is a set of slots with a queue of its own: <slot>-<n>.lock files and <ticket>-* tickets. A run of one test file is light: it
// takes one of HANDLINE_LIGHT_SLOTS (default 2) light slots, and a wait of 1s or more goes to <dir>/waits.log.
type class struct {
	name, slot, ticket string
	slots              func() int
}

var (
	heavyClass = class{"heavy", "slot", "ticket", heavyslots.Slots}
	lightClass = class{"light", "light", "light-ticket", heavyslots.LightSlots}
)

var testFile = regexp.MustCompile(`(^|/)[^/.][^/]*\.(test|spec)\.[cm]?[jt]sx?$`)

// isLight is whether the command is vitest run on one named test file, which needs no more than a type check does. A filter that is not
// a whole test file (vitest takes substrings) or a second argument may run the suite.
func isLight(args []string) bool {
	if path.Base(args[0]) != "vitest" {
		return false
	}
	var files []string
	for _, a := range args[1:] {
		if a != "run" && !strings.HasPrefix(a, "-") {
			files = append(files, a)
		}
	}
	return len(files) == 1 && testFile.MatchString(files[0])
}

// acquire waits for a free heavy slot and returns its locked file, which holds the slot until it is closed or the process ends.
func acquire() (*os.File, int, error) { return acquireClass(heavyClass) }

func acquireClass(c class) (*os.File, int, error) {
	began := time.Now()
	f, n, err := acquireIn(c)
	if wait := time.Since(began); err == nil && wait >= time.Second {
		logWait(c, wait)
	}
	return f, n, err
}

// logWait appends one line per wait: epoch seconds, class, agent, seconds waited.
func logWait(c class, wait time.Duration) {
	d, err := heavyslots.Dir()
	if err != nil {
		return
	}
	f, err := os.OpenFile(filepath.Join(d, "waits.log"), os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	agent := os.Getenv("OCP_AGENT")
	if agent == "" {
		agent = "-"
	}
	fmt.Fprintf(f, "%d %s %s %d\n", time.Now().Unix(), c.name, agent, int(wait.Seconds()))
}

func acquireIn(c class) (*os.File, int, error) {
	d, err := heavyslots.Dir()
	if err != nil {
		return nil, 0, err
	}
	if err := os.MkdirAll(d, 0o755); err != nil {
		return nil, 0, err
	}
	ticket, err := takeTicket(d, c.ticket)
	if err != nil {
		return nil, 0, err
	}
	defer ticket.drop()
	me, last, spoke := holder(), "", false
	for {
		ahead := ticket.ahead(c.ticket)
		var held, said []string
		// Only the first in line looks at memory and disk; it keeps its place while it waits for them.
		low := ""
		if ahead == 0 {
			if low = lowMemory(); low == "" {
				low = lowDisk()
			}
		}
		for n := 1; low == "" && n <= c.slots(); n++ {
			f, err := os.OpenFile(filepath.Join(d, fmt.Sprintf("%s-%d.lock", c.slot, n)), os.O_RDWR|os.O_CREATE, 0o644)
			if err != nil {
				return nil, 0, err
			}
			// Only the first in line may take a free slot; the others look at who holds them.
			err = syscall.EWOULDBLOCK
			if ahead == 0 {
				err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
			}
			if err == nil {
				f.Truncate(0)
				f.WriteAt([]byte(me), 0)
				return f, n, nil
			}
			who, _ := os.ReadFile(f.Name())
			f.Close()
			if !errors.Is(err, syscall.EWOULDBLOCK) {
				return nil, 0, err
			}
			held = append(held, string(who))
			said = append(said, describe(strings.TrimSpace(string(who))))
		}
		if now := strings.Join(held, "\x00") + fmt.Sprint(ahead) + low; !spoke || now != last {
			line := strings.Join(said, "; ")
			if low != "" {
				line = "waiting: " + low
			}
			if ahead > 0 {
				line += fmt.Sprintf("; %d waiting before this run", ahead)
			}
			if low != "" {
				fmt.Fprintf(os.Stderr, "heavyslot: %s\n", line)
			} else {
				fmt.Fprintf(os.Stderr, "heavyslot: waiting for a %s-run slot (%d slots): %s\n", c.name, c.slots(), line)
			}
			last = now
			spoke = true
		}
		time.Sleep(poll)
	}
}

// A ticket is a waiter's place in line: a file ticket-<nanoseconds>-<pid>, which the waiter keeps locked until it has a slot or ends.
// It is locked under a name no waiter reads and renamed (the lock stays), so no waiter ever finds a ticket that is not yet locked.
type ticket struct {
	dir  string
	name string
	f    *os.File
}

func takeTicket(dir, prefix string) (*ticket, error) {
	name := fmt.Sprintf("%s-%020d-%d", prefix, time.Now().UnixNano(), os.Getpid())
	pending := filepath.Join(dir, fmt.Sprintf("pending-%d", os.Getpid()))
	f, err := os.OpenFile(pending, os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err == nil {
		err = os.Rename(pending, filepath.Join(dir, name))
	}
	if err != nil {
		f.Close()
		os.Remove(pending)
		return nil, err
	}
	return &ticket{dir: dir, name: name, f: f}, nil
}

func (t *ticket) drop() {
	os.Remove(filepath.Join(t.dir, t.name))
	t.f.Close()
}

// ahead is how many live waiters hold an earlier ticket. A ticket nobody holds the lock of belongs to a run that died: it is removed.
func (t *ticket) ahead(prefix string) int {
	names, _ := filepath.Glob(filepath.Join(t.dir, prefix+"-*"))
	sort.Strings(names)
	n := 0
	for _, path := range names {
		if filepath.Base(path) >= t.name {
			break
		}
		f, err := os.Open(path)
		if err != nil {
			continue
		}
		if syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) == nil {
			os.Remove(path)
		} else {
			n++
		}
		f.Close()
	}
	return n
}

// holder is this run as a slot file records it: agent, worktree, pid and since (epoch seconds), one per line.
func holder() string {
	agent := os.Getenv("OCP_AGENT")
	if agent == "" {
		agent = "-"
	}
	wd, _ := os.Getwd()
	if top, err := exec.Command("git", "rev-parse", "--show-toplevel").Output(); err == nil {
		wd = strings.TrimSpace(string(top))
	}
	return fmt.Sprintf("agent %s\nworktree %s\npid %d\nsince %d\n", agent, wd, os.Getpid(), time.Now().Unix())
}

func describe(record string) string {
	f := map[string]string{}
	for _, line := range strings.Split(record, "\n") {
		k, v, _ := strings.Cut(line, " ")
		f[k] = v
	}
	since, err := strconv.ParseInt(f["since"], 10, 64)
	if err != nil {
		return "held (its holder is still writing who it is)"
	}
	at := time.Unix(since, 0)
	return fmt.Sprintf("held by %s in %s, pid %s, since %s (%s)", f["agent"], f["worktree"], f["pid"], at.Format("15:04:05"),
		time.Since(at).Round(time.Second))
}

// run starts the command, passes it the signals this process gets, and returns its exit status. Off a terminal the command gets a
// process group of its own and a signal goes to all of it: a stopped run stops everything it started, not only its shell, which
// would let its current step run to the end. On a terminal it stays in the terminal's foreground group, which Ctrl-C reaches whole,
// and out of which a step that sets the terminal's modes or reads it would be stopped for good.
func run(args []string) int {
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	group := !hlclient.StdinIsTerminal()
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: group}
	if err := cmd.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "heavyslot: %v\n", err)
		return 127
	}
	sigs := make(chan os.Signal, 4)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)
	go func() {
		for s := range sigs {
			if group {
				syscall.Kill(-cmd.Process.Pid, s.(syscall.Signal))
			} else {
				cmd.Process.Signal(s)
			}
		}
	}()
	err := cmd.Wait()
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		if ws, ok := exit.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			return 128 + int(ws.Signal())
		}
		return exit.ExitCode()
	}
	if err != nil {
		return 1
	}
	return 0
}
