package turns

import (
	"syscall"

	"golang.org/x/sys/unix"
)

// pipe is a wake-up pipe whose ends a child the daemon starts does not inherit. Darwin has no pipe2, so both ends are made close-on-exec
// under the fork lock, before any fork can copy them.
func pipe(p *[2]int) error {
	syscall.ForkLock.RLock()
	defer syscall.ForkLock.RUnlock()
	if err := unix.Pipe(p[:]); err != nil {
		return err
	}
	unix.CloseOnExec(p[0])
	unix.CloseOnExec(p[1])
	return nil
}

// zombie is whether pid has exited and waits to be reaped: the process table still lists it, but it is no session.
func zombie(pid int) bool {
	k, err := unix.SysctlKinfoProc("kern.proc.pid", pid)
	return err == nil && int(k.Proc.P_pid) == pid && k.Proc.P_stat == 5 // SZOMB
}
