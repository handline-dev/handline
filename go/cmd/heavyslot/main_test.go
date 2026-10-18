package main

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
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
	cmd.Env = append(os.Environ(), append([]string{"HANDLINE_HEAVY_DIR=" + slots, "HANDLINE_HEAVY_SLOT=", "HANDLINE_HEAVY_SLOTS=",
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
// waited says who holds the slot.
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
	said := aerr.String() + berr.String()
	if !strings.Contains(said, "waiting for a heavy-run slot") || !strings.Contains(said, "held by tester in ") || !strings.Contains(said, ", pid ") {
		t.Errorf("the waiter did not say who holds the slot: %q", said)
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

// A holder killed outright frees its slot: the kernel drops its lock, no one has to clean up after it.
func TestAKilledHolderFreesItsSlot(t *testing.T) {
	bin, slots := build(t)
	a, _ := start(t, bin, slots, nil, "sleep", "60")
	waitHeld(t, slots)
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
	case <-time.After(3 * poll):
		t.Fatalf("the slot stayed held after its holder was killed: %s", berr)
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
