package main

import (
	"bufio"
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
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
	case <-time.After(2 * poll):
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
