package turns

import (
	"sync"

	"golang.org/x/sys/unix"
)

// watchExit calls exited from its own goroutine once pid has exited, unless stop came first. A pid already gone counts as exited.
func watchExit(pid int, exited func()) (stop func(), err error) {
	kq, err := unix.Kqueue()
	if err != nil {
		return nil, err
	}
	var p [2]int
	if err := unix.Pipe(p[:]); err != nil {
		unix.Close(kq)
		return nil, err
	}
	ev := make([]unix.Kevent_t, 2)
	unix.SetKevent(&ev[0], pid, unix.EVFILT_PROC, unix.EV_ADD|unix.EV_ONESHOT)
	ev[0].Fflags = unix.NOTE_EXIT
	unix.SetKevent(&ev[1], p[0], unix.EVFILT_READ, unix.EV_ADD)
	if _, err := unix.Kevent(kq, ev, nil, nil); err != nil {
		for _, c := range []int{kq, p[0], p[1]} {
			unix.Close(c)
		}
		if err == unix.ESRCH {
			go exited()
			return func() {}, nil
		}
		return nil, err
	}
	go func() {
		defer unix.Close(kq)
		defer unix.Close(p[0])
		got := make([]unix.Kevent_t, 2)
		for {
			n, err := unix.Kevent(kq, nil, got, nil)
			if err == unix.EINTR {
				continue
			}
			if err != nil {
				return
			}
			for _, e := range got[:n] {
				if int(e.Ident) == p[0] {
					return
				}
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
