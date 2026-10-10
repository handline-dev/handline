// Package pairsock connects a client to a test daemon with no socket file, since a sandbox may refuse every bind:
// each connection is a socketpair, made in process or sent by a child over a unix socket it inherited.
package pairsock

import (
	"net"
	"os"
	"sync"

	"golang.org/x/sys/unix"
)

// Pair is two connected unix stream sockets.
func Pair() (net.Conn, net.Conn, error) {
	fds, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_STREAM, 0)
	if err != nil {
		return nil, nil, err
	}
	a, err := fileConn(fds[0])
	if err != nil {
		unix.Close(fds[1])
		return nil, nil, err
	}
	b, err := fileConn(fds[1])
	if err != nil {
		a.Close()
		return nil, nil, err
	}
	return a, b, nil
}

// fileConn is the connection on fd, which it takes over.
func fileConn(fd int) (net.Conn, error) {
	f := os.NewFile(uintptr(fd), "socketpair")
	defer f.Close()
	return net.FileConn(f)
}

// DialOver is a child's dial: a fresh socketpair, one end sent over ctl to the Listener whose Control it inherited.
// That end is closed only once the Listener says it holds it: macOS can drop a descriptor whose sender closed it in
// flight.
func DialOver(ctl *net.UnixConn) func() (net.Conn, error) {
	return func() (net.Conn, error) {
		fds, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_STREAM, 0)
		if err != nil {
			return nil, err
		}
		defer unix.Close(fds[1])
		_, _, err = ctl.WriteMsgUnix([]byte{0}, unix.UnixRights(fds[1]), nil)
		if err == nil {
			_, err = ctl.Read(make([]byte, 1))
		}
		if err != nil {
			unix.Close(fds[0])
			return nil, err
		}
		return fileConn(fds[0])
	}
}

// Listener accepts the connections its Dial makes and those sent over its Control.
type Listener struct {
	conns chan net.Conn
	done  chan struct{}
	once  sync.Once
	mu    sync.Mutex
	ctl   []*os.File
}

func NewListener() *Listener {
	return &Listener{conns: make(chan net.Conn), done: make(chan struct{})}
}

func (l *Listener) Accept() (net.Conn, error) {
	select {
	case c := <-l.conns:
		return c, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}

func (l *Listener) Close() error {
	l.once.Do(func() {
		close(l.done)
		l.mu.Lock()
		defer l.mu.Unlock()
		for _, f := range l.ctl {
			f.Close()
		}
	})
	return nil
}

func (l *Listener) Addr() net.Addr { return &net.UnixAddr{Name: "socketpair", Net: "unix"} }

// Dial connects in process.
func (l *Listener) Dial() (net.Conn, error) {
	a, b, err := Pair()
	if err != nil {
		return nil, err
	}
	if !l.queue(b) {
		a.Close()
		return nil, net.ErrClosed
	}
	return a, nil
}

func (l *Listener) queue(c net.Conn) bool {
	select {
	case l.conns <- c:
		return true
	case <-l.done:
		c.Close()
		return false
	}
}

// Control is the end a child inherits (exec.Cmd.ExtraFiles) and dials over with DialOver; it stays open until Close.
func (l *Listener) Control() (*os.File, error) {
	fds, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_STREAM, 0)
	if err != nil {
		return nil, err
	}
	mine, err := fileConn(fds[0])
	if err != nil {
		unix.Close(fds[1])
		return nil, err
	}
	child := os.NewFile(uintptr(fds[1]), "pairsock-control")
	l.mu.Lock()
	l.ctl = append(l.ctl, child)
	l.mu.Unlock()
	go func() {
		<-l.done
		mine.Close()
	}()
	go l.receive(mine.(*net.UnixConn))
	return child, nil
}

// receive queues each connection sent over ctl, one byte and one descriptor at a time, and answers a byte once it
// holds it.
func (l *Listener) receive(ctl *net.UnixConn) {
	buf, oob := make([]byte, 1), make([]byte, unix.CmsgSpace(4))
	for {
		_, oobn, _, _, err := ctl.ReadMsgUnix(buf, oob)
		if err != nil {
			return
		}
		msgs, _ := unix.ParseSocketControlMessage(oob[:oobn])
		for _, m := range msgs {
			fds, _ := unix.ParseUnixRights(&m)
			for _, fd := range fds {
				c, err := fileConn(fd)
				ctl.Write([]byte{0})
				if err == nil {
					l.queue(c)
				}
			}
		}
	}
}
