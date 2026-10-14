package turns

import (
	"sync"

	"golang.org/x/sys/unix"
)

// watchFile calls changed from its own goroutine each time path is written, until stop; done runs when that goroutine ends.
func watchFile(path string, changed, done func()) (stop func(), err error) {
	in, err := unix.InotifyInit1(unix.IN_CLOEXEC | unix.IN_NONBLOCK)
	if err != nil {
		return nil, err
	}
	if _, err := unix.InotifyAddWatch(in, path, unix.IN_MODIFY); err != nil {
		unix.Close(in)
		return nil, err
	}
	var p [2]int
	if err := unix.Pipe2(p[:], unix.O_CLOEXEC); err != nil {
		unix.Close(in)
		return nil, err
	}
	go func() {
		defer func() {
			unix.Close(in)
			unix.Close(p[0])
			done()
		}()
		changed() // a write between the open and the watch
		buf := make([]byte, 4096)
		for {
			fds := []unix.PollFd{{Fd: int32(in), Events: unix.POLLIN}, {Fd: int32(p[0]), Events: unix.POLLIN}}
			if _, err := unix.Poll(fds, -1); err == unix.EINTR {
				continue
			} else if err != nil || fds[1].Revents != 0 {
				return
			}
			for {
				if n, _ := unix.Read(in, buf); n <= 0 {
					break
				}
			}
			changed()
		}
	}()
	var once sync.Once
	return func() { once.Do(func() { unix.Close(p[1]) }) }, nil
}
