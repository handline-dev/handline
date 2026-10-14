package turns

import (
	"sync"

	"golang.org/x/sys/unix"
)

// watchFile calls changed from its own goroutine each time path is written, until stop; done runs when that goroutine ends.
func watchFile(path string, changed, done func()) (stop func(), err error) {
	fd, err := unix.Open(path, unix.O_EVTONLY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	kq, err := unix.Kqueue()
	if err != nil {
		unix.Close(fd)
		return nil, err
	}
	var p [2]int
	if err := unix.Pipe(p[:]); err != nil {
		unix.Close(fd)
		unix.Close(kq)
		return nil, err
	}
	ev := make([]unix.Kevent_t, 2)
	unix.SetKevent(&ev[0], fd, unix.EVFILT_VNODE, unix.EV_ADD|unix.EV_CLEAR)
	ev[0].Fflags = unix.NOTE_WRITE | unix.NOTE_EXTEND
	unix.SetKevent(&ev[1], p[0], unix.EVFILT_READ, unix.EV_ADD)
	if _, err := unix.Kevent(kq, ev, nil, nil); err != nil {
		for _, c := range []int{fd, kq, p[0], p[1]} {
			unix.Close(c)
		}
		return nil, err
	}
	go func() {
		defer func() {
			for _, c := range []int{fd, kq, p[0]} {
				unix.Close(c)
			}
			done()
		}()
		changed() // a write between the open and the watch
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
			changed()
		}
	}()
	var once sync.Once
	return func() { once.Do(func() { unix.Close(p[1]) }) }, nil
}
