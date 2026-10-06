// Package peer asks the kernel who is on the other end of a unix socket and what a process descends from.
package peer

import "net"

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
