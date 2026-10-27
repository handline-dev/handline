// Package sysres is what the machine and each agent use, measured every Every and kept for daemon.status: per agent, the CPU and resident
// memory summed over the process tree under its terminal's pane (one ps read for all of them), and for the machine its RAM, its load
// against its cores and the free space of the volume the home is on.
package sysres

import (
	"fmt"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Every is how often the sampler measures.
const Every = 15 * time.Second

// Use is one agent's process tree: CPU in percent of one core, as ps gives it, and resident memory in bytes.
type Use struct {
	CPU float64
	RSS int64
}

// Machine is the whole machine; a figure that could not be read is 0.
type Machine struct {
	RAMTotal, RAMUsed, RAMFree int64
	Load1                      float64
	Cores                      int
	DiskFree, DiskTotal        int64
	// Simulator is the name of a running phone simulator or emulator ("" for none): the heaviest thing a test of the app starts.
	Simulator string
}

// Snapshot is one measurement.
type Snapshot struct {
	At      int64
	Agents  map[string]Use
	Machine Machine
}

// Read measures this machine's RAM and load once.
func Read() (Machine, error) { return thisMachine() }

// Sampler measures and keeps the latest snapshot.
type Sampler struct {
	Home string
	// Panes is each agent's pane process, the root of its tree; agents with no terminal are left out.
	Panes func() map[string]int
	// PS is the process table as `ps -axo pid=,ppid=,%cpu=,rss=,comm=` prints it; nil runs ps.
	PS func() (string, error)
	// Machine reads the machine's RAM and load; nil reads this one.
	Machine func() (Machine, error)
	// Disk is the free and total bytes of the volume holding a path; nil is statfs.
	Disk func(path string) (free, total int64, err error)
	Log  func(string)

	mu   sync.Mutex
	last *Snapshot
}

type proc struct {
	ppid int
	cpu  float64
	rss  int64
	comm string // the command name or path, "" when the line has none
}

// parsePS reads the process table; a line that does not start with four numbers is skipped, and the rest of it is the command.
func parsePS(out string) map[int]proc {
	procs := map[int]proc{}
	for _, line := range strings.Split(out, "\n") {
		f := strings.Fields(line)
		if len(f) < 4 {
			continue
		}
		pid, e1 := strconv.Atoi(f[0])
		ppid, e2 := strconv.Atoi(f[1])
		cpu, e3 := strconv.ParseFloat(f[2], 64)
		rss, e4 := strconv.ParseInt(f[3], 10, 64)
		if e1 == nil && e2 == nil && e3 == nil && e4 == nil {
			procs[pid] = proc{ppid: ppid, cpu: cpu, rss: rss * 1024, comm: strings.Join(f[4:], " ")}
		}
	}
	return procs
}

// deviceSimulator names a running phone simulator or emulator, "" for none, by the command's name alone: the iOS Simulator app, a booted
// simulated device (launchd_sim), the Android emulator, or the qemu it runs on. Look-alikes (CoreSimulatorService, an editor open on
// an "emulator" file) are other names and do not match.
func deviceSimulator(procs map[int]proc) string {
	for _, p := range procs {
		name := p.comm
		if i := strings.LastIndex(name, "/"); i >= 0 {
			name = name[i+1:]
		}
		switch {
		case name == "Simulator" || name == "launchd_sim":
			return "iOS simulator"
		case name == "emulator" || strings.HasPrefix(name, "qemu-system-"):
			return "Android emulator"
		}
	}
	return ""
}

// treeUse sums each root's process and all its descendants.
func treeUse(procs map[int]proc, roots map[string]int) map[string]Use {
	kids := map[int][]int{}
	for pid, p := range procs {
		kids[p.ppid] = append(kids[p.ppid], pid)
	}
	out := map[string]Use{}
	for agent, root := range roots {
		if _, ok := procs[root]; !ok {
			continue
		}
		var u Use
		for stack, seen := []int{root}, map[int]bool{}; len(stack) > 0; {
			pid := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			if seen[pid] {
				continue
			}
			seen[pid] = true
			u.CPU += procs[pid].cpu
			u.RSS += procs[pid].rss
			stack = append(stack, kids[pid]...)
		}
		out[agent] = u
	}
	return out
}

// Sample measures once and keeps it as the latest.
func (s *Sampler) Sample(now int64) error {
	ps := s.PS
	if ps == nil {
		ps = func() (string, error) {
			out, err := exec.Command("ps", "-axo", "pid=,ppid=,%cpu=,rss=,comm=").Output()
			return string(out), err
		}
	}
	table, err := ps()
	if err != nil {
		return fmt.Errorf("reading the process table: %w", err)
	}
	procs := parsePS(table)
	snap := &Snapshot{At: now, Agents: treeUse(procs, s.Panes())}
	machine := s.Machine
	if machine == nil {
		machine = thisMachine
	}
	if snap.Machine, err = machine(); err != nil && s.Log != nil {
		s.Log("resources: " + err.Error())
	}
	snap.Machine.Cores = runtime.NumCPU()
	snap.Machine.Simulator = deviceSimulator(procs)
	disk := s.Disk
	if disk == nil {
		disk = StatfsFree
	}
	if snap.Machine.DiskFree, snap.Machine.DiskTotal, err = disk(s.Home); err != nil && s.Log != nil {
		s.Log("resources: disk: " + err.Error())
	}
	s.mu.Lock()
	s.last = snap
	s.mu.Unlock()
	return nil
}

// Run samples every Every until stop is closed.
func (s *Sampler) Run(stop <-chan struct{}) {
	t := time.NewTicker(Every)
	defer t.Stop()
	for {
		if err := s.Sample(time.Now().Unix()); err != nil && s.Log != nil {
			s.Log("resources: " + err.Error())
		}
		select {
		case <-stop:
			return
		case <-t.C:
		}
	}
}

// LatestMachine is the machine in the latest snapshot; nil before the first.
func (s *Sampler) LatestMachine() *Machine {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.last == nil {
		return nil
	}
	m := s.last.Machine
	return &m
}

// Latest is the latest snapshot as daemon.status serves it; nil before the first.
func (s *Sampler) Latest() map[string]any {
	s.mu.Lock()
	snap := s.last
	s.mu.Unlock()
	if snap == nil {
		return nil
	}
	agents := map[string]any{}
	var total Use
	for name, u := range snap.Agents {
		agents[name] = map[string]any{"cpu_pct": u.CPU, "rss_bytes": u.RSS}
		total.CPU += u.CPU
		total.RSS += u.RSS
	}
	m := snap.Machine
	machine := map[string]any{
		"ram_total_bytes": m.RAMTotal, "ram_used_bytes": m.RAMUsed, "ram_free_bytes": m.RAMFree, "load_1m": m.Load1, "cores": m.Cores,
		"disk_free_bytes": m.DiskFree, "disk_total_bytes": m.DiskTotal, "load_per_core": 0.0, "ram_free_pct": 0}
	if m.Cores > 0 {
		machine["load_per_core"] = m.Load1 / float64(m.Cores)
	}
	if m.RAMTotal > 0 {
		machine["ram_free_pct"] = int(m.RAMFree * 100 / m.RAMTotal)
	}
	return map[string]any{"at": time.Unix(snap.At, 0).UTC().Format(time.RFC3339), "agents": agents,
		"agents_total": map[string]any{"cpu_pct": total.CPU, "rss_bytes": total.RSS}, "machine": machine}
}

// StatfsFree is the free (to this user) and total bytes of the volume holding path.
func StatfsFree(path string) (int64, int64, error) {
	var s syscall.Statfs_t
	if err := syscall.Statfs(path, &s); err != nil {
		return 0, 0, err
	}
	return int64(s.Bavail) * int64(s.Bsize), int64(s.Blocks) * int64(s.Bsize), nil
}
