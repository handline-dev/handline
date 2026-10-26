package sysres

import (
	"errors"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
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
	if all := got["agents_total"].(map[string]any); all["cpu_pct"] != 97.0 || all["rss_bytes"] != int64((1024+2048+204800+10240+512)*1024) {
		t.Fatalf("the total is every agent's: %v", all)
	}
	if got["machine"].(map[string]any)["ram_free_pct"] != 25 {
		t.Fatalf("a quarter of the memory is free: %v", got["machine"])
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
	// The load is the system's own 1-minute average as its tools print it, read a moment apart: close, not equal.
	want := systemLoad1(t)
	if diff := m.Load1 - want; diff > 0.5+want/10 || diff < -(0.5+want/10) {
		t.Fatalf("load %.2f, the system says %.2f", m.Load1, want)
	}
}

// systemLoad1 is the 1-minute load as `sysctl -n vm.loadavg` ("{ 1.23 1.10 1.00 }") or /proc/loadavg print it.
func systemLoad1(t *testing.T) float64 {
	t.Helper()
	var out []byte
	var err error
	if runtime.GOOS == "darwin" {
		out, err = exec.Command("sysctl", "-n", "vm.loadavg").Output()
	} else {
		out, err = os.ReadFile("/proc/loadavg")
	}
	if err != nil {
		t.Fatal(err)
	}
	f := strings.Fields(strings.Trim(strings.TrimSpace(string(out)), "{}"))
	if len(f) == 0 {
		t.Fatalf("%q", out)
	}
	load, err := strconv.ParseFloat(f[0], 64)
	if err != nil {
		t.Fatal(err)
	}
	return load
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
