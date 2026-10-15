// Package peer asks the kernel who is on the other end of a unix socket and what a process descends from.
package peer

import (
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

// Proc is a process as the kernel reports it: its parent and its start time, which tells a reused pid apart.
type Proc struct {
	PPid  int
	Start string
}

// PID is the process id of the other end of a unix connection; 0 when it cannot be told.
func PID(c net.Conn) int {
	uc, ok := c.(*net.UnixConn)
	if !ok {
		return 0
	}
	raw, err := uc.SyscallConn()
	if err != nil {
		return 0
	}
	pid := 0
	_ = raw.Control(func(fd uintptr) { pid = peerPID(int(fd)) })
	return pid
}

// Terminal is the controlling terminal of pid as its /dev path; "" when it has none or it cannot be told.
func Terminal(pid int) string {
	dev, ok := ttyDev(pid)
	if !ok {
		return ""
	}
	for _, dir := range []string{"/dev", "/dev/pts"} {
		entries, _ := os.ReadDir(dir)
		for _, e := range entries {
			if dir == "/dev" && !strings.HasPrefix(e.Name(), "tty") && e.Name() != "console" {
				continue
			}
			path := filepath.Join(dir, e.Name())
			if fi, err := os.Stat(path); err == nil && fi.Mode()&os.ModeCharDevice != 0 {
				if st, ok := fi.Sys().(*syscall.Stat_t); ok && uint64(st.Rdev) == dev {
					return path
				}
			}
		}
	}
	return "?" // a terminal, though no /dev entry names it
}
