package main

import (
	"bufio"
	"bytes"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

func build(t *testing.T) (bin, slots string) {
	t.Helper()
	dir := t.TempDir()
	bin = filepath.Join(dir, "heavyslot")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, out)
	}
	return bin, filepath.Join(dir, "slots")
}

func start(t *testing.T, bin, slots string, env []string, args ...string) (*exec.Cmd, *bytes.Buffer) {
	t.Helper()
	cmd := exec.Command(bin, append([]string{"--"}, args...)...)
	var errb bytes.Buffer
	cmd.Stderr = &errb
	cmd.Env = append(os.Environ(), append([]string{"HANDLINE_HEAVY_DIR=" + slots, "HANDLINE_HEAVY_SLOT=", "HANDLINE_HEAVY_SLOTS=1",
		"OCP_AGENT=tester"}, env...)...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) })
	return cmd, &errb
}

func waitHeld(t *testing.T, slots string) {
	t.Helper()
	for end := time.Now().Add(10 * time.Second); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		if b, _ := os.ReadFile(filepath.Join(slots, "slot-1.lock")); strings.Contains(string(b), "since ") {
			return
		}
	}
	t.Fatal("the first run never took the slot")
}

// Two runs started together take the one slot in turn: each fails if it finds the other inside (the mkdir), and the one that
// waited says so.
func TestTwoRunsStartedTogetherRunOneAfterTheOther(t *testing.T) {
	bin, slots := build(t)
	inside := filepath.Join(t.TempDir(), "inside")
	body := "mkdir " + inside + " || exit 9; sleep 1; rmdir " + inside
	began := time.Now()
	a, aerr := start(t, bin, slots, nil, "sh", "-c", body)
	b, berr := start(t, bin, slots, nil, "sh", "-c", body)
	for _, c := range []*exec.Cmd{a, b} {
		if err := c.Wait(); err != nil {
			t.Fatalf("a run found the other inside its slot: %v\n%s%s", err, aerr, berr)
		}
	}
	if took := time.Since(began); took < 2*time.Second {
		t.Errorf("both ran in %v: not one after the other", took)
	}
	if said := aerr.String() + berr.String(); !strings.Contains(said, "waiting for a heavy-run slot") {
		t.Errorf("the waiter did not say it waits: %q", said)
	}
}

// A waiter that finds a slot held before its holder has written who it is still says it waits, at once.
func TestAWaiterSaysItWaitsBeforeTheHolderWritesWhoItIs(t *testing.T) {
	bin, slots := build(t)
	if err := os.MkdirAll(slots, 0o755); err != nil {
		t.Fatal(err)
	}
	lock, err := os.OpenFile(filepath.Join(slots, "slot-1.lock"), os.O_RDWR|os.O_CREATE, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(bin, "--", "true")
	cmd.Env = append(os.Environ(), "HANDLINE_HEAVY_DIR="+slots, "HANDLINE_HEAVY_SLOT=", "HANDLINE_HEAVY_SLOTS=1", "OCP_AGENT=tester")
	stderr, err := cmd.StderrPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cmd.Process.Kill() })
	line := make(chan string, 1)
	go func() {
		l, _ := bufio.NewReader(stderr).ReadString('\n')
		line <- l
	}()
	select {
	case l := <-line:
		if !strings.Contains(l, "waiting for a heavy-run slot") || !strings.Contains(l, "still writing") {
			t.Errorf("the waiter did not say it waits: %q", l)
		}
	case <-time.After(5 * poll):
		t.Fatal("the waiter said nothing while the slot was held")
	}
	syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	if err := cmd.Wait(); err != nil {
		t.Fatal(err)
	}
}

// HANDLINE_HEAVY_SLOTS=2 lets two run at once.
func TestTwoSlotsLetTwoRunsAtOnce(t *testing.T) {
	bin, slots := build(t)
	d := t.TempDir()
	// Each says it is in, then waits for the other to be in too: one after the other, the first gives up.
	both := func(me, other string) string {
		return "touch " + d + "/" + me + "; i=0; while [ ! -e " + d + "/" + other + " ]; do i=$((i+1)); [ $i -gt 100 ] && exit 9; sleep 0.1; done"
	}
	a, _ := start(t, bin, slots, []string{"HANDLINE_HEAVY_SLOTS=2"}, "sh", "-c", both("a", "b"))
	b, _ := start(t, bin, slots, []string{"HANDLINE_HEAVY_SLOTS=2"}, "sh", "-c", both("b", "a"))
	if ea, eb := a.Wait(), b.Wait(); ea != nil || eb != nil {
		t.Errorf("two slots did not run at once: %v %v", ea, eb)
	}
}

// A holder killed outright frees its slot: the kernel drops its lock, no one has to clean up after it. The waiter says who held it.
func TestAKilledHolderFreesItsSlot(t *testing.T) {
	bin, slots := build(t)
	pidFile := filepath.Join(t.TempDir(), "pid")
	a, _ := start(t, bin, slots, nil, "sh", "-c", `echo $$ >"$0"; exec sleep 60`, pidFile)
	waitHeld(t, slots)
	t.Cleanup(func() { syscall.Kill(-readPid(t, pidFile), syscall.SIGKILL) }) // the sleep's own group, which the holder's kill misses
	b, berr := start(t, bin, slots, nil, "true")
	time.Sleep(300 * time.Millisecond)
	if err := a.Process.Kill(); err != nil { // the holder only: its sleep lives on, and must not hold the slot
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- b.Wait() }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("%v %s", err, berr)
		}
		if said := berr.String(); !strings.Contains(said, "held by tester in ") || !strings.Contains(said, ", pid ") {
			t.Errorf("the waiter did not say who holds the slot: %q", said)
		}
	case <-time.After(3 * poll):
		t.Fatalf("the slot stayed held after its holder was killed: %s", berr)
	}
}

// A run whose starter died (a session that ended) ends too: its command is killed and the slot is free for the next run.
func TestARunWhoseParentDiedKillsItsCommandAndFreesTheSlot(t *testing.T) {
	bin, slots := build(t)
	pidFile := filepath.Join(t.TempDir(), "pid")
	// The starter puts the run in the background and exits, so the run is re-parented.
	starter := exec.Command("sh", "-c", `"$0" -- sh -c 'echo $$ >"$0"; exec sleep 60' "$1" & sleep 0.5`, bin, pidFile)
	starter.Env = append(os.Environ(), "HANDLINE_HEAVY_DIR="+slots, "HANDLINE_HEAVY_SLOT=", "HANDLINE_HEAVY_SLOTS=1", "OCP_AGENT=tester")
	if err := starter.Run(); err != nil {
		t.Fatal(err)
	}
	sleep := readPid(t, pidFile)
	t.Cleanup(func() { syscall.Kill(sleep, syscall.SIGKILL) })
	// The next run gets the slot only once the first has ended, and its command looks at once: the killed command must be gone, not a
	// zombie that still answers kill -0.
	b, berr := start(t, bin, slots, nil, "sh", "-c", `! kill -0 "$0"`, strconv.Itoa(sleep))
	if err := b.Wait(); err != nil {
		t.Fatalf("the command outlived the run's parent: %v %s", err, berr)
	}
}

// A run still in line when its starter dies has no command to kill: it ends at once, and the slot goes on to the next run.
func TestAQueuedRunWhoseParentDiedEndsWithoutTheSlot(t *testing.T) {
	bin, slots := build(t)
	holder, _ := start(t, bin, slots, nil, "sleep", "60")
	waitHeld(t, slots)
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	// The starter leaves once the run has taken its ticket (it is then past reading its parent), with the run's stderr on the pipe.
	starter := exec.Command("sh", "-c", `"$0" -- true & while ! ls "$1"/ticket-* >/dev/null 2>&1; do sleep 0.02; done`, bin, slots)
	starter.Env = append(os.Environ(), "HANDLINE_HEAVY_DIR="+slots, "HANDLINE_HEAVY_SLOT=", "HANDLINE_HEAVY_SLOTS=1", "OCP_AGENT=tester")
	starter.Stderr = w
	err = starter.Run()
	w.Close()
	if err != nil {
		t.Fatal(err)
	}
	// The pipe closes when the last holder of its write end, the run, is gone.
	said := make(chan string, 1)
	go func() { b, _ := io.ReadAll(r); said <- string(b) }()
	select {
	case s := <-said:
		if !strings.Contains(s, "the process that started this run is gone") {
			t.Errorf("the run did not say why it ended: %q", s)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the queued run did not end when its parent died")
	}
	if syscall.Kill(holder.Process.Pid, 0) != nil {
		t.Fatal("the first run ended too")
	}
	holder.Process.Kill()
	b, berr := start(t, bin, slots, nil, "true")
	if err := b.Wait(); err != nil {
		t.Fatalf("%v %s", err, berr)
	}
}

// readPid waits for the pid a command wrote to path.
func readPid(t *testing.T, path string) int {
	t.Helper()
	for end := time.Now().Add(10 * time.Second); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		if b, err := os.ReadFile(path); err == nil {
			if pid, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil && pid > 0 {
				return pid
			}
		}
	}
	t.Fatal("the command never wrote its pid")
	return 0
}

// A signal to the run reaches everything the command started, not only the command: a step it was in stops at once.
func TestASignalStopsEverythingTheRunStarted(t *testing.T) {
	bin, slots := build(t)
	pidFile := filepath.Join(t.TempDir(), "pid")
	c, _ := start(t, bin, slots, nil, "sh", "-c", `sleep 60 & echo $! >"$0"; wait`, pidFile)
	sleep := readPid(t, pidFile)
	t.Cleanup(func() { syscall.Kill(sleep, syscall.SIGKILL) })
	if err := c.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	// Before Wait, which a child that outlived the signal holds up through the stderr pipe it shares.
	for end := time.Now().Add(5 * time.Second); syscall.Kill(sleep, 0) == nil; time.Sleep(20 * time.Millisecond) {
		if time.Now().After(end) {
			t.Fatal("the command's child outlived the signal")
		}
	}
	c.Wait()
}

// On a terminal the command stays in the terminal's foreground group: a step that sets the terminal's modes is not stopped.
func TestOnATerminalAStepMaySetItsModes(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("no python3 to open a pty with")
	}
	bin, slots := build(t)
	// The child of forkpty runs heavyslot on the pty; the parent prints what it reads until the child exits.
	script := `import os, sys
pid, fd = os.forkpty()
if pid == 0:
    os.execv(sys.argv[1], sys.argv[1:])
out = b""
while True:
    try:
        b = os.read(fd, 1024)
    except OSError:
        break
    if not b:
        break
    out += b
os.waitpid(pid, 0)
sys.stdout.write(out.decode(errors="replace"))
`
	cmd := exec.Command(py, "-c", script, bin, "--", "sh", "-c", "stty -echo; stty echo; echo ok")
	cmd.Env = append(os.Environ(), "HANDLINE_HEAVY_DIR="+slots, "HANDLINE_HEAVY_SLOT=", "HANDLINE_HEAVY_SLOTS=1", "OCP_AGENT=tester")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	var out bytes.Buffer
	cmd.Stdout = &out
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) })
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		if err != nil || !strings.Contains(out.String(), "ok") {
			t.Fatalf("%v %q", err, out.String())
		}
	case <-time.After(20 * time.Second):
		t.Fatal("a step that set the terminal's modes hung")
	}
}

// The command's exit status is the run's, and a run inside a slot does not wait on its own slot.
func TestTheExitStatusIsTheCommandsAndANestedRunDoesNotWait(t *testing.T) {
	bin, slots := build(t)
	c, _ := start(t, bin, slots, nil, "sh", "-c", "exit 3")
	if err := c.Wait(); c.ProcessState.ExitCode() != 3 {
		t.Fatalf("exit %d: %v", c.ProcessState.ExitCode(), err)
	}
	outer, _ := start(t, bin, slots, nil, bin, "--", "true")
	done := make(chan error, 1)
	go func() { done <- outer.Wait() }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("a run inside the slot waited on its own slot")
	}
}

func waitTickets(t *testing.T, slots string, n int) {
	t.Helper()
	for end := time.Now().Add(10 * time.Second); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		if m, _ := filepath.Glob(filepath.Join(slots, "ticket-*")); len(m) >= n {
			return
		}
	}
	t.Fatalf("fewer than %d runs took a ticket", n)
}

// Waiters take the slot in the order they came, and a run that starts again the moment it ends goes to the back of the line.
func TestWaitersAreServedInTheOrderTheyCame(t *testing.T) {
	bin, slots := build(t)
	log := filepath.Join(t.TempDir(), "order")
	note := func(name string) string { return "echo " + name + " >> " + log }
	holder, _ := start(t, bin, slots, nil, "sh", "-c", "sleep 1.5")
	waitHeld(t, slots)
	var waiters []*exec.Cmd
	for i, name := range []string{"first", "second", "third"} {
		c, _ := start(t, bin, slots, nil, "sh", "-c", note(name)+"; sleep 0.3")
		waiters = append(waiters, c)
		waitTickets(t, slots, i+1)
	}
	if err := holder.Wait(); err != nil {
		t.Fatal(err)
	}
	again, _ := start(t, bin, slots, nil, "sh", "-c", note("holder-again"))
	for _, c := range append(waiters, again) {
		if err := c.Wait(); err != nil {
			t.Fatal(err)
		}
	}
	got, _ := os.ReadFile(log)
	if want := "first\nsecond\nthird\nholder-again\n"; string(got) != want {
		t.Errorf("the runs went in the order %q, want %q", got, want)
	}
}

// A ticket whose waiter died does not hold up the line, and is removed.
func TestADeadWaitersTicketIsDropped(t *testing.T) {
	bin, slots := build(t)
	if err := os.MkdirAll(slots, 0o755); err != nil {
		t.Fatal(err)
	}
	dead := filepath.Join(slots, "ticket-00000000000000000001-99999")
	if err := os.WriteFile(dead, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	c, _ := start(t, bin, slots, nil, "true")
	if err := c.Wait(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(dead); !os.IsNotExist(err) {
		t.Errorf("the dead waiter's ticket is still there: %v", err)
	}
	if m, _ := filepath.Glob(filepath.Join(slots, "ticket-*")); len(m) != 0 {
		t.Errorf("tickets left behind: %v", m)
	}
}

// stderrOf runs f with os.Stderr captured and returns what it wrote after f's goroutine is told to stop reading.
func captureStderr(t *testing.T) (read func() string) {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	old := os.Stderr
	os.Stderr = w
	var mu sync.Mutex
	var buf bytes.Buffer
	done := make(chan struct{})
	go func() {
		b := make([]byte, 4096)
		for {
			n, err := r.Read(b)
			mu.Lock()
			buf.Write(b[:n])
			mu.Unlock()
			if err != nil {
				close(done)
				return
			}
		}
	}()
	t.Cleanup(func() { os.Stderr = old; w.Close(); <-done })
	return func() string { mu.Lock(); defer mu.Unlock(); return buf.String() }
}

func memoryEnv(t *testing.T, config string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HANDLINE_HOME", home)
	t.Setenv("HANDLINE_HEAVY_DIR", filepath.Join(t.TempDir(), "slots"))
	t.Setenv("HANDLINE_HEAVY_SLOTS", "1")
	t.Setenv("OCP_AGENT", "tester")
	if config != "" {
		if err := os.WriteFile(filepath.Join(home, "config.yaml"), []byte(config), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	oldPoll, oldMem, oldDisk := poll, freeMemory, freeDisk
	poll = 10 * time.Millisecond
	freeDisk = func(string) (int64, int64, error) { return 500 * gb, 1000 * gb, nil }
	t.Cleanup(func() { poll, freeMemory, freeDisk = oldPoll, oldMem, oldDisk })
}

func TestAFreeSlotIsNotTakenWhileFreeMemoryIsLowAndIsOnceItIsBack(t *testing.T) {
	memoryEnv(t, "")
	var mu sync.Mutex
	free := int64(2)
	freeMemory = func() (int64, int64, error) { mu.Lock(); defer mu.Unlock(); return free, 100, nil }
	said := captureStderr(t)
	got := make(chan error, 1)
	go func() {
		f, _, err := acquire()
		if f != nil {
			f.Close()
		}
		got <- err
	}()
	for end := time.Now().Add(5 * time.Second); !strings.Contains(said(), "waiting: free memory 2% under 10%") && time.Now().Before(end); time.Sleep(10 * time.Millisecond) {
	}
	if !strings.Contains(said(), "heavyslot: waiting: free memory 2% under 10%") {
		t.Fatalf("no waiter line naming the memory: %q", said())
	}
	select {
	case <-got:
		t.Fatal("took a free slot while memory was low")
	case <-time.After(10 * poll):
	}
	mu.Lock()
	free = 30
	mu.Unlock()
	select {
	case err := <-got:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("still waiting after memory was back")
	}
	if n := strings.Count(said(), "waiting: free memory"); n != 1 {
		t.Errorf("the line was said %d times, want once: %q", n, said())
	}
}

func TestTheMemoryLimitIsTheLoadGatesKey(t *testing.T) {
	memoryEnv(t, "ram_free_min_pct: 40\n")
	freeMemory = func() (int64, int64, error) { return 30, 100, nil }
	if got := lowMemory(); got != "free memory 30% under 40%" {
		t.Errorf("lowMemory = %q", got)
	}
	memoryEnv(t, "ram_free_min_pct: 0\n")
	freeMemory = func() (int64, int64, error) { return 1, 100, nil }
	if got := lowMemory(); got != "" {
		t.Errorf("a limit of 0 held the run: %q", got)
	}
}

func TestAMemoryReadingThatFailsDoesNotHoldTheRun(t *testing.T) {
	memoryEnv(t, "")
	freeMemory = func() (int64, int64, error) { return 0, 0, errors.New("no vm_stat") }
	if got := lowMemory(); got != "" {
		t.Errorf("lowMemory = %q", got)
	}
}

func TestOnlyTheFirstInLineLooksAtMemory(t *testing.T) {
	memoryEnv(t, "")
	dir := os.Getenv("HANDLINE_HEAVY_DIR")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	front, err := os.OpenFile(filepath.Join(dir, "ticket-00000000000000000001-1"), os.O_RDWR|os.O_CREATE, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer front.Close()
	if err := syscall.Flock(int(front.Fd()), syscall.LOCK_EX); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	reads := 0
	freeMemory = func() (int64, int64, error) { mu.Lock(); defer mu.Unlock(); reads++; return 50, 100, nil }
	captureStderr(t)
	done := make(chan struct{})
	go func() {
		if f, _, err := acquire(); err == nil {
			f.Close()
		}
		close(done)
	}()
	time.Sleep(10 * poll)
	mu.Lock()
	n := reads
	mu.Unlock()
	if n != 0 {
		t.Errorf("a waiter with a run ahead of it read memory %d times", n)
	}
	front.Close() // the run ahead ends: this one is first, reads memory and takes the slot
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("never took the slot once first in line")
	}
	mu.Lock()
	defer mu.Unlock()
	if reads == 0 {
		t.Error("the first in line did not read memory")
	}
}

func TestTheDiskFloorIsAConfigKeyAndAReadingThatFailsOrIsOffDoesNotHold(t *testing.T) {
	memoryEnv(t, "")
	freeDisk = func(string) (int64, int64, error) { return 3 * gb, 100 * gb, nil }
	if got := lowDisk(); got != "free disk 3 GB under 5 GB" {
		t.Errorf("the default floor is 5 GB: %q", got)
	}
	freeDisk = func(string) (int64, int64, error) { return 5 * gb, 100 * gb, nil }
	if got := lowDisk(); got != "" {
		t.Errorf("at the floor holds: %q", got)
	}
	memoryEnv(t, "disk_free_min_gb: 20\n")
	freeDisk = func(string) (int64, int64, error) { return 15 * gb, 100 * gb, nil }
	if got := lowDisk(); got != "free disk 15 GB under 20 GB" {
		t.Errorf("the key sets the floor: %q", got)
	}
	memoryEnv(t, "disk_free_min_gb: 0\n")
	freeDisk = func(string) (int64, int64, error) { return 0, 100 * gb, nil }
	if got := lowDisk(); got != "" {
		t.Errorf("0 is off: %q", got)
	}
	memoryEnv(t, "")
	freeDisk = func(string) (int64, int64, error) { return 0, 0, errors.New("statfs") }
	if got := lowDisk(); got != "" {
		t.Errorf("an error held the run: %q", got)
	}
}

func TestAFreeSlotWaitsWhileFreeDiskIsLowAndGoesOnceItIsBack(t *testing.T) {
	memoryEnv(t, "")
	freeMemory = func() (int64, int64, error) { return 90, 100, nil }
	var mu sync.Mutex
	free := int64(1) * gb
	freeDisk = func(string) (int64, int64, error) { mu.Lock(); defer mu.Unlock(); return free, 100 * gb, nil }
	said := captureStderr(t)
	got := make(chan error, 1)
	go func() {
		f, _, err := acquire()
		if f != nil {
			f.Close()
		}
		got <- err
	}()
	select {
	case <-got:
		t.Fatal("took a slot with the disk under the floor")
	case <-time.After(200 * time.Millisecond):
	}
	if !strings.Contains(said(), "waiting: free disk 1 GB under 5 GB") {
		t.Errorf("it did not say why: %q", said())
	}
	mu.Lock()
	free = 50 * gb
	mu.Unlock()
	select {
	case err := <-got:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("still waiting after the disk was back")
	}
}

func TestOnlyOneWholeTestFileIsLight(t *testing.T) {
	for args, want := range map[string]bool{"vitest run a.test.ts": true, "/x/node_modules/.bin/vitest run src/b.spec.tsx": true,
		"vitest run --reporter=dot src/a.test.ts": true, "vitest run": false, "vitest run src": false, "vitest run .test.ts": false,
		"vitest run a.test.ts src": false, "vitest run a.test.ts b.test.ts": false, "go test a.test.ts": false, "tsc a.test.ts": false, "node a.test.ts": false} {
		if got := isLight(strings.Fields(args)); got != want {
			t.Errorf("isLight(%q) = %v, want %v", args, got, want)
		}
	}
}

func lightEnv(t *testing.T) {
	memoryEnv(t, "")
	t.Setenv("HANDLINE_HEAVY_SLOTS", "2")
	t.Setenv("HANDLINE_LIGHT_SLOTS", "2")
	freeMemory = func() (int64, int64, error) { return 50, 100, nil }
}

func take(t *testing.T, c class) *os.File {
	t.Helper()
	f, _, err := acquireClass(c)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.Close() })
	return f
}

func TestALightRunGoesWhileBothHeavySlotsAreHeld(t *testing.T) {
	lightEnv(t)
	take(t, heavyClass)
	take(t, heavyClass)
	done := make(chan error, 1)
	go func() {
		f, _, err := acquireClass(lightClass)
		if f != nil {
			f.Close()
		}
		done <- err
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("a light run waited behind the heavy ones")
	}
}

func TestLightRunsAreCappedAndAHeavyRunDoesNotTakeTheirSlots(t *testing.T) {
	bin, slots := build(t)
	env := []string{"HANDLINE_HEAVY_SLOTS=2", "HANDLINE_LIGHT_SLOTS=2"}
	vitest := filepath.Join(t.TempDir(), "vitest")
	if err := os.WriteFile(vitest, []byte("#!/bin/sh\n"+`[ -n "$HOLD" ] && sleep 30`+"\nexit 0\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	for _, f := range []string{"a.test.ts", "b.test.ts"} {
		holder, _ := start(t, bin, slots, append([]string{"HOLD=1"}, env...), vitest, f)
		// The command has a process group of its own, which only the helper's own signal handling reaches.
		t.Cleanup(func() { holder.Process.Signal(syscall.SIGTERM); holder.Wait() })
	}
	for end := time.Now().Add(10 * time.Second); ; time.Sleep(20 * time.Millisecond) {
		a, _ := os.ReadFile(filepath.Join(slots, "light-1.lock"))
		b, _ := os.ReadFile(filepath.Join(slots, "light-2.lock"))
		if strings.Contains(string(a), "since ") && strings.Contains(string(b), "since ") {
			break
		}
		if time.Now().After(end) {
			t.Fatal("the two light runs never held their slots")
		}
	}
	third, said := start(t, bin, slots, env, vitest, "c.test.ts")
	heavy, _ := start(t, bin, slots, env, "sh", "-c", "exit 0")
	if err := heavy.Wait(); err != nil {
		t.Fatalf("a heavy run waited for the light slots: %v", err)
	}
	exited := make(chan error, 1)
	go func() { exited <- third.Wait() }()
	select {
	case <-exited:
		t.Fatal("a third light run went with two held")
	case <-time.After(10 * poll):
	}
	syscall.Kill(-third.Process.Pid, syscall.SIGKILL)
	<-exited
	if !strings.Contains(said.String(), "waiting for a light-run slot") {
		t.Errorf("the third light run did not say it waits: %q", said)
	}
}

func TestAWaitOfASecondIsLoggedAndAnInstantTakeIsNot(t *testing.T) {
	lightEnv(t)
	log := filepath.Join(os.Getenv("HANDLINE_HEAVY_DIR"), "waits.log")
	take(t, lightClass)
	held := take(t, lightClass)
	go func() { time.Sleep(1200 * time.Millisecond); held.Close() }()
	take(t, lightClass)
	b, _ := os.ReadFile(log)
	f := strings.Fields(string(b))
	if len(f) != 4 || f[1] != "light" || f[2] != "tester" || f[3] != "1" {
		t.Fatalf("waits.log = %q, want one line: epoch light tester 1", b)
	}
	if _, err := strconv.ParseInt(f[0], 10, 64); err != nil {
		t.Errorf("the first field is not epoch seconds: %q", f[0])
	}
	os.Remove(log)
	take(t, heavyClass)
	if b, err := os.ReadFile(log); err == nil {
		t.Errorf("an instant take was logged: %q", b)
	}
}
