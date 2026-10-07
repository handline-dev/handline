package peer

import (
	"os"
	"strconv"
	"strings"
	"time"

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

// Started is when pid began, from its start tick (100 per second after boot) and the boot time in /proc/stat; ok is
// false when it cannot be told.
func Started(pid int) (time.Time, bool) {
	p, ok := Lookup(pid)
	if !ok {
		return time.Time{}, false
	}
	ticks, err := strconv.ParseInt(p.Start, 10, 64)
	if err != nil {
		return time.Time{}, false
	}
	b, err := os.ReadFile("/proc/stat")
	if err != nil {
		return time.Time{}, false
	}
	for _, l := range strings.Split(string(b), "\n") {
		if v, ok := strings.CutPrefix(l, "btime "); ok {
			boot, err := strconv.ParseInt(strings.TrimSpace(v), 10, 64)
			if err != nil {
				return time.Time{}, false
			}
			return time.Unix(boot, 0).Add(time.Duration(ticks) * 10 * time.Millisecond), true
		}
	}
	return time.Time{}, false
}
