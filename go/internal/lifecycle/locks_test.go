package lifecycle

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/session"
)

type fakeProcs struct {
	rows []session.Process
	envs map[int]string
}

func (f fakeProcs) Table() ([]session.Process, bool) { return f.rows, true }
func (f fakeProcs) ProcessEnv(pid int) (string, bool) {
	e, ok := f.envs[pid]
	return e, ok
}

func TestOnlyTheProgramAtTheFrontOfTheLineIsADaemon(t *testing.T) {
	home := t.TempDir()
	l := &Locks{Home: home, PID: 1, Procs: fakeProcs{rows: []session.Process{
		{PID: 10, Command: "/usr/local/bin/ocp daemon"},
		{PID: 11, Command: "/bin/bash /x/ocp daemon --x"},
		{PID: 12, Command: "grep -n ocp daemon"},
		{PID: 13, Command: "vim /x/ocp daemon.py"},
		{PID: 1, Command: "/usr/local/bin/ocp daemon"},
		{PID: 14, Command: "/a/b/c/ocp daemon"},
	}, envs: map[int]string{10: "OCP_HOME=" + home, 11: "HANDLINE_HOME=" + home, 14: "OCP_HOME=" + t.TempDir()}}}
	got := l.ProcessList()
	if len(got) != 2 || got[0] != 10 || got[1] != 11 {
		t.Fatalf("%v", got)
	}
}

func TestADaemonOfAnotherHomeIsNotADuplicateAndAnUnreadableOneIs(t *testing.T) {
	home := t.TempDir()
	l := &Locks{Home: home, PID: 1, Procs: fakeProcs{rows: []session.Process{
		{PID: 20, Command: "/x/ocp daemon"}, {PID: 21, Command: "/x/ocp daemon"},
	}, envs: map[int]string{20: "OCP_HOME=" + t.TempDir()}}}
	if got := l.ProcessList(); len(got) != 1 || got[0] != 21 {
		t.Fatalf("%v", got)
	}
}

func TestHomeIsComparedAsADirectoryNotASpelling(t *testing.T) {
	home := t.TempDir()
	link := filepath.Join(t.TempDir(), "link")
	if err := os.Symlink(home, link); err != nil {
		t.Fatal(err)
	}
	l := &Locks{Home: home, PID: 1, Procs: fakeProcs{rows: []session.Process{{PID: 30, Command: "/x/ocp daemon"}}, envs: map[int]string{30: "OCP_HOME=" + link}}}
	if got := l.ProcessList(); len(got) != 1 {
		t.Fatalf("%v", got)
	}
}

func TestRunningElsewhereIsTheHoldersPidOrTheTableOrNone(t *testing.T) {
	home := t.TempDir()
	l := &Locks{Home: home, PID: 1, Procs: fakeProcs{rows: []session.Process{{PID: 40, Command: "/x/ocp daemon"}}, envs: map[int]string{40: "OCP_HOME=" + home}}}
	if pid, ok := l.RunningElsewhere(); ok || pid != 0 {
		t.Fatalf("no lock held: %d %v", pid, ok)
	}
	h := lock.TakeSingleton(home, DaemonLock)
	if h == nil {
		t.Fatal("no hold")
	}
	defer h.Release()
	// the same process holds it in this test, so flock by a second description reports it held with our pid
	if pid, ok := l.RunningElsewhere(); !ok || pid != os.Getpid() {
		t.Fatalf("held: %d %v", pid, ok)
	}
}
