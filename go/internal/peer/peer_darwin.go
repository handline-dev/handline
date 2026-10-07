package peer

import (
	"fmt"
	"time"

	"golang.org/x/sys/unix"
)

const solLocal, localPeerPID = 0, 2

func peerPID(fd int) int {
	pid, err := unix.GetsockoptInt(fd, solLocal, localPeerPID)
	if err != nil {
		return 0
	}
	return pid
}

// Lookup is the parent and start time of pid, from the kernel; ok is false when no such process runs.
// The start time is spelled as handline/infra/sessiontoken.py spells it, seconds.microseconds.
func Lookup(pid int) (Proc, bool) {
	k, err := unix.SysctlKinfoProc("kern.proc.pid", pid)
	if err != nil || int(k.Proc.P_pid) != pid {
		return Proc{}, false
	}
	st := k.Proc.P_starttime
	return Proc{PPid: int(k.Eproc.Ppid), Start: fmt.Sprintf("%d.%06d", st.Sec, st.Usec)}, true
}

// Started is when pid began; ok is false when no such process runs.
func Started(pid int) (time.Time, bool) {
	k, err := unix.SysctlKinfoProc("kern.proc.pid", pid)
	if err != nil || int(k.Proc.P_pid) != pid {
		return time.Time{}, false
	}
	return time.Unix(int64(k.Proc.P_starttime.Sec), int64(k.Proc.P_starttime.Usec)*1000), true
}
