package sysres

import (
	"os"
	"strconv"
	"strings"
)

// thisMachine reads RAM from /proc/meminfo (free is MemAvailable) and the load from /proc/loadavg.
func thisMachine() (Machine, error) {
	var m Machine
	info, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return m, err
	}
	for _, line := range strings.Split(string(info), "\n") {
		f := strings.Fields(line)
		if len(f) < 2 {
			continue
		}
		kb, _ := strconv.ParseInt(f[1], 10, 64)
		switch f[0] {
		case "MemTotal:":
			m.RAMTotal = kb * 1024
		case "MemAvailable:":
			m.RAMFree = kb * 1024
		}
	}
	m.RAMUsed = m.RAMTotal - m.RAMFree
	if load, err := os.ReadFile("/proc/loadavg"); err == nil {
		if f := strings.Fields(string(load)); len(f) > 0 {
			m.Load1, _ = strconv.ParseFloat(f[0], 64)
		}
	}
	return m, nil
}
