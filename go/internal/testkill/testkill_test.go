package testkill

import (
	"bufio"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// started runs a shell that starts a grandchild and prints its pid, as the leader of a group of its own.
func started(t *testing.T) int {
	t.Helper()
	cmd := exec.Command("sh", "-c", "sleep 60 & echo $!; wait")
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := Group(t, cmd); err != nil {
		t.Fatal(err)
	}
	line, err := bufio.NewReader(out).ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(line))
	if err != nil {
		t.Fatal(err)
	}
	return pid
}

func gone(pid int) bool {
	for range 50 {
		if syscall.Kill(pid, 0) != nil {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return false
}

func TestTheWholeGroupIsKilledWhenTheTestEnds(t *testing.T) {
	var pid int
	t.Run("starts", func(t *testing.T) { pid = started(t) })
	if !gone(pid) {
		syscall.Kill(pid, syscall.SIGKILL)
		t.Fatalf("the grandchild %d outlived its test", pid)
	}
}

// An interrupted test binary runs no cleanups, so the signal itself must take the group down.
func TestTheWholeGroupIsKilledWhenTheBinaryIsInterrupted(t *testing.T) {
	if os.Getenv("TESTKILL_CHILD") == "1" {
		pid := started(t)
		os.Stdout.WriteString("pid " + strconv.Itoa(pid) + "\n")
		syscall.Kill(os.Getpid(), syscall.SIGINT)
		time.Sleep(10 * time.Second)
		t.Fatal("the interrupt did not end the binary")
	}
	exe, _ := os.Executable()
	child := exec.Command(exe, "-test.run=^TestTheWholeGroupIsKilledWhenTheBinaryIsInterrupted$", "-test.v")
	child.Env = append(os.Environ(), "TESTKILL_CHILD=1")
	out, _ := child.Output() // it ends by the signal, so not with success
	var pid int
	for _, l := range strings.Split(string(out), "\n") {
		if n, ok := strings.CutPrefix(l, "pid "); ok {
			pid, _ = strconv.Atoi(n)
		}
	}
	if pid == 0 {
		t.Fatalf("the child said no pid: %s", out)
	}
	if !gone(pid) {
		syscall.Kill(pid, syscall.SIGKILL)
		t.Fatalf("the grandchild %d outlived the interrupted binary", pid)
	}
}

// A fake CLI whose test binary is gone, without having stopped it, takes its own group down.
func TestAFakeWhoseParentIsGoneKillsItsGroup(t *testing.T) {
	self, _ := os.Executable()
	pidFile := os.Getenv("TESTKILL_PID")
	if pidFile == "" {
		pidFile = t.TempDir() + "/pid"
	}
	again := func(mode string) *exec.Cmd {
		c := exec.Command(self, "-test.run=^TestAFakeWhoseParentIsGoneKillsItsGroup$")
		c.Env = append(os.Environ(), "TESTKILL_MODE="+mode, "TESTKILL_PID="+pidFile)
		return c
	}
	switch os.Getenv("TESTKILL_MODE") {
	case "fake": // started in a group of its own, as the session code starts a CLI
		ExitWithParent()
		sleep := exec.Command("sleep", "60")
		sleep.Start()
		os.WriteFile(pidFile, []byte(strconv.Itoa(sleep.Process.Pid)), 0o644)
		select {}
	case "parent": // starts the fake and ends without stopping it
		fake := again("fake")
		fake.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		fake.Start()
		time.Sleep(time.Second)
		os.Exit(0)
	}
	again("parent").Run()
	out, _ := os.ReadFile(pidFile)
	pid, err := strconv.Atoi(string(out))
	if err != nil {
		t.Fatalf("no pid: %q", out)
	}
	if !gone(pid) {
		syscall.Kill(pid, syscall.SIGKILL)
		t.Fatalf("the fake's child %d outlived the binary that started the fake", pid)
	}
}
