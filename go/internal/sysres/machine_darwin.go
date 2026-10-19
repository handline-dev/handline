package sysres

import (
	"encoding/binary"
	"os/exec"

	"golang.org/x/sys/unix"
)

// thisMachine reads RAM from hw.memsize and vm_stat (free is the free, inactive and speculative pages, which macOS hands out on demand)
// and the load from vm.loadavg.
func thisMachine() (Machine, error) {
	var m Machine
	total, err := unix.SysctlUint64("hw.memsize")
	if err != nil {
		return m, err
	}
	m.RAMTotal = int64(total)
	out, err := exec.Command("vm_stat").Output()
	if err != nil {
		return m, err
	}
	m.RAMFree = vmStatFree(string(out))
	m.RAMUsed = m.RAMTotal - m.RAMFree
	if raw, err := unix.SysctlRaw("vm.loadavg"); err == nil && len(raw) >= 24 {
		// struct loadavg { fixpt_t ldavg[3]; long fscale; }: the first fixed-point average over the scale, which is padded to offset 16.
		ld := float64(binary.LittleEndian.Uint32(raw[0:4]))
		scale := float64(binary.LittleEndian.Uint64(raw[16:24]))
		if scale > 0 {
			m.Load1 = ld / scale
		}
	}
	return m, nil
}
