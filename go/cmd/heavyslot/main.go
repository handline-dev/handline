// heavyslot runs a command once it holds one of the machine's heavy-run slots: heavyslot -- <command> [args...]
//
// A slot is an flock on <dir>/slot-<n>.lock (dir: HANDLINE_HEAVY_DIR, else ~/.handline/heavy-slots; n up to HANDLINE_HEAVY_SLOTS,
// default 1). The kernel lets go of the lock when its holder dies, so a killed run frees its slot by itself. The holder writes
// who it is into the lock file, and a waiter prints that whenever the holders change. The command runs with HANDLINE_HEAVY_SLOT
// set, so a heavy script it starts runs in the same slot rather than waiting on itself. When the slots cannot be kept (no
// writable directory), it says so and runs the command anyway: the slots spread load, they must never stop a check.
package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/heavyslots"
)

var poll = 2 * time.Second

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
		if lock, n, err := acquire(); err != nil {
			fmt.Fprintf(os.Stderr, "heavyslot: runs without a slot: %v\n", err)
		} else {
			defer lock.Close()
			os.Setenv("HANDLINE_HEAVY_SLOT", strconv.Itoa(n))
		}
	}
	os.Exit(run(args))
}

// acquire waits for a free slot and returns its locked file, which holds the slot until it is closed or the process ends.
func acquire() (*os.File, int, error) {
	d, err := heavyslots.Dir()
	if err != nil {
		return nil, 0, err
	}
	if err := os.MkdirAll(d, 0o755); err != nil {
		return nil, 0, err
	}
	me, last := holder(), ""
	for {
		var held, said []string
		for n := 1; n <= heavyslots.Slots(); n++ {
			f, err := os.OpenFile(filepath.Join(d, fmt.Sprintf("slot-%d.lock", n)), os.O_RDWR|os.O_CREATE, 0o644)
			if err != nil {
				return nil, 0, err
			}
			err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
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
		if now := strings.Join(held, "\x00"); now != last {
			fmt.Fprintf(os.Stderr, "heavyslot: waiting for a heavy-run slot (HANDLINE_HEAVY_SLOTS=%d): %s\n", heavyslots.Slots(), strings.Join(said, "; "))
			last = now
		}
		time.Sleep(poll)
	}
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

// run starts the command, passes it the signals this process gets, and returns its exit status.
func run(args []string) int {
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := cmd.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "heavyslot: %v\n", err)
		return 127
	}
	sigs := make(chan os.Signal, 4)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)
	go func() {
		for s := range sigs {
			cmd.Process.Signal(s)
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
