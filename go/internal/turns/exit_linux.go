package turns

import (
	"fmt"
	"os"
	"strings"
	"sync"

	"golang.org/x/sys/unix"
)

// watchExit calls exited from its own goroutine once pid has exited, unless stop came first. A pid already gone counts as exited.
func watchExit(pid int, exited func()) (stop func(), err error) {
	pfd, err := unix.PidfdOpen(pid, 0)
	if err == unix.ESRCH {
		go exited()
		return func() {}, nil
	}
	if err != nil {
		return nil, err
	}
	var p [2]int
	if err := unix.Pipe2(p[:], unix.O_CLOEXEC); err != nil {
		unix.Close(pfd)
		return nil, err
	}
	go func() {
		defer unix.Close(pfd)
		defer unix.Close(p[0])
		for {
			fds := []unix.PollFd{{Fd: int32(pfd), Events: unix.POLLIN}, {Fd: int32(p[0]), Events: unix.POLLIN}}
			if _, err := unix.Poll(fds, -1); err == unix.EINTR {
				continue
			} else if err != nil || fds[1].Revents != 0 {
				return
			}
			exited()
			return
		}
	}()
	var once sync.Once
	return func() { once.Do(func() { unix.Close(p[1]) }) }, nil
}

// reaped is whether pid has left the process table, not merely exited.
func reaped(pid int) bool { return unix.Kill(pid, 0) == unix.ESRCH }

// zombie is whether pid has exited and waits to be reaped: the process table still lists it, but it is no session.
func zombie(pid int) bool {
	b, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return false
	}
	s := string(b)
	i := strings.LastIndexByte(s, ')') // the command name may hold spaces and parentheses
	return i >= 0 && i+2 < len(s) && s[i+2] == 'Z'
}
