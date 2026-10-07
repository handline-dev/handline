package lock

import (
	"os"
	"path/filepath"
	"testing"
)

func TestATerminalHoldDoesNotNestAndEndsWithRelease(t *testing.T) {
	home := t.TempDir()
	h := TakeTerminal(home, "worker")
	if h == nil {
		t.Fatal("not taken")
	}
	if TakeTerminal(home, "worker") != nil {
		t.Fatal("a hold nested")
	}
	if !TerminalBusy(home, "worker") {
		t.Fatal("a held terminal is busy")
	}
	if TakeTerminal(home, "other") == nil {
		t.Fatal("another agent's terminal is free")
	}
	h.Release()
	if TerminalBusy(home, "worker") {
		t.Fatal("released but busy")
	}
	if again := TakeTerminal(home, "worker"); again == nil {
		t.Fatal("not free after release")
	} else {
		again.Release()
	}
	if TerminalBusy(home, "nobody") {
		t.Fatal("no lock file is not a holder")
	}
}

func TestSingletonRecordsItsHolder(t *testing.T) {
	home := t.TempDir()
	if _, ok := Holder(home, "daemon"); ok {
		t.Fatal("a holder from nowhere")
	}
	h := TakeSingleton(home, "daemon")
	if h == nil {
		t.Fatal("not taken")
	}
	if pid, ok := Holder(home, "daemon"); !ok || pid != os.Getpid() {
		t.Fatalf("holder %d %v", pid, ok)
	}
	if TakeSingleton(home, "daemon") != nil {
		t.Fatal("two daemons")
	}
	b, _ := os.ReadFile(filepath.Join(home, "locks", "daemon.lock"))
	if len(b) != pidWidth+1 {
		t.Fatalf("the pid is written at a fixed width: %q", b)
	}
	h.Release()
	if _, ok := Holder(home, "daemon"); ok {
		t.Fatal("a name left in the file is not a live holder")
	}
}

func TestAnUnattemptableLockIsNotTaken(t *testing.T) {
	file := filepath.Join(t.TempDir(), "file")
	os.WriteFile(file, nil, 0o600)
	if TakeSingleton(file, "daemon") != nil {
		t.Fatal("a lock taken where no folder can be made")
	}
}
