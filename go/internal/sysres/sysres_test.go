package sysres

import (
	"errors"
	"os"
	"runtime"
	"testing"
	"time"
)

// The fake process table: agent a's pane 10 runs a shell (11) with claude (12) under it, which runs a test (13); b's pane 20 is alone;
// 30 belongs to no agent.
const table = `   10     1   0.5   1024
   11    10   0.0   2048
   12    11  40.0  204800
   13    12  55.5  10240
   20     1   1.0   512
   30     1  99.0  99999
garbage line`

func fixed(m Machine) func() (Machine, error) { return func() (Machine, error) { return m, nil } }

func TestEachAgentIsItsPanesWholeProcessTree(t *testing.T) {
	s := &Sampler{Home: "/", PS: func() (string, error) { return table, nil },
		Panes:   func() map[string]int { return map[string]int{"a": 10, "b": 20, "gone": 99} },
		Machine: fixed(Machine{RAMTotal: 16 << 30, RAMUsed: 12 << 30, RAMFree: 4 << 30, Load1: 3.5}),
		Disk:    func(string) (int64, int64, error) { return 9 << 30, 500 << 30, nil }}
	if s.Latest() != nil {
		t.Fatal("nothing before the first sample")
	}
	if err := s.Sample(1_790_000_000); err != nil {
		t.Fatal(err)
	}
	got := s.Latest()
	agents := got["agents"].(map[string]any)
	a := agents["a"].(map[string]any)
	if a["cpu_pct"] != 96.0 || a["rss_bytes"] != int64((1024+2048+204800+10240)*1024) {
		t.Fatalf("a sums 10, 11, 12 and 13: %v", a)
	}
	if b := agents["b"].(map[string]any); b["cpu_pct"] != 1.0 || b["rss_bytes"] != int64(512*1024) {
		t.Fatalf("b is its pane alone: %v", b)
	}
	if _, ok := agents["gone"]; ok {
		t.Fatal("a pane with no process is left out")
	}
	m := got["machine"].(map[string]any)
	if m["ram_free_bytes"] != int64(4<<30) || m["load_1m"] != 3.5 || m["cores"] != runtime.NumCPU() || m["disk_free_bytes"] != int64(9<<30) ||
		got["at"] != "2026-09-21T14:13:20Z" {
		t.Fatalf("%v", got)
	}
}

func TestAMachineOrDiskThatCannotBeReadLeavesItsFiguresAtZero(t *testing.T) {
	var logged []string
	s := &Sampler{Home: "/", PS: func() (string, error) { return table, nil }, Panes: func() map[string]int { return nil },
		Machine: func() (Machine, error) { return Machine{}, errors.New("no sysctl") },
		Disk:    func(string) (int64, int64, error) { return 0, 0, errors.New("no statfs") },
		Log:     func(s string) { logged = append(logged, s) }}
	if err := s.Sample(1); err != nil || len(logged) != 2 {
		t.Fatalf("%v %v", err, logged)
	}
	if s.Latest()["machine"].(map[string]any)["ram_total_bytes"] != int64(0) {
		t.Fatal(s.Latest())
	}
	s.PS = func() (string, error) { return "", errors.New("no ps") }
	if err := s.Sample(2); err == nil {
		t.Fatal("no process table is a failed sample")
	}
}

func TestVMStatFreeIsFreeInactiveAndSpeculativePages(t *testing.T) {
	out := "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:                               100.\n" +
		"Pages active:                             900.\nPages inactive:                           50.\nPages speculative:                        10.\n"
	if got := vmStatFree(out); got != 160*16384 {
		t.Fatal(got)
	}
}

func TestThisMachineReadsItsRAM(t *testing.T) {
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		t.Skip("read on macOS and Linux only")
	}
	m, err := thisMachine()
	if err != nil || m.RAMTotal <= 0 || m.RAMFree <= 0 || m.RAMUsed <= 0 || m.Load1 < 0 {
		t.Fatalf("%+v %v", m, err)
	}
	t.Logf("%+v", m)
}

func TestSampleReadsTheRealProcessTable(t *testing.T) {
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		t.Skip("read on macOS and Linux only")
	}
	s := &Sampler{Home: t.TempDir(), Panes: func() map[string]int { return map[string]int{"me": os.Getpid()} }}
	began := time.Now()
	if err := s.Sample(time.Now().Unix()); err != nil {
		t.Fatal(err)
	}
	took := time.Since(began)
	me := s.Latest()["agents"].(map[string]any)["me"].(map[string]any)
	if me["rss_bytes"].(int64) <= 0 || s.Latest()["machine"].(map[string]any)["disk_total_bytes"].(int64) <= 0 {
		t.Fatalf("%v", s.Latest())
	}
	t.Logf("one sample took %v", took)
}
