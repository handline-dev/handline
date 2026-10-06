package peer

import (
	"os"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
)

func peerPID(fd int) int {
	u, err := unix.GetsockoptUcred(fd, unix.SOL_SOCKET, unix.SO_PEERCRED)
	if err != nil {
		return 0
	}
	return int(u.Pid)
}

// Lookup is the parent and start time (in ticks) of pid, from /proc; ok is false when no such process runs.
func Lookup(pid int) (Proc, bool) {
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return Proc{}, false
	}
	s := string(b)
	i := strings.LastIndex(s, ")")
	if i < 0 {
		return Proc{}, false
	}
	f := strings.Fields(s[i+1:])
	if len(f) < 20 {
		return Proc{}, false
	}
	ppid, err := strconv.Atoi(f[1])
	if err != nil {
		return Proc{}, false
	}
	return Proc{PPid: ppid, Start: f[19]}, true
}
