package daemon

import (
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// alwaysNative are the methods a gateway serves itself whatever Native says: the handshake, and the devices, which
// are its own.
var alwaysNative = map[string]bool{"hello": true, "whoami": true, "device.pair": true, "device.redeem": true,
	"device.revoke": true, "devices.list": true}

func checkGateway(cfg Config) error {
	switch {
	case cfg.Listen == "" || cfg.CertFile == "" || cfg.KeyFile == "":
		return errors.New("a gateway needs an address to listen on and the certificate and key to serve (-listen, -cert, -key)")
	case cfg.RelaySocket == "" || cfg.PidFile == "":
		return errors.New("a gateway needs the relay socket and its own pid file")
	case cfg.SocketFile != "" && filepath.Clean(cfg.SocketFile) == filepath.Join(cfg.Home, "daemon.sock"):
		return errors.New("a gateway's local socket must not be the Python daemon's daemon.sock")
	}
	return nil
}

// gatewayMethods leaves a gateway server with the methods it serves natively. The local server of a gateway
// is only for the founder's device commands.
func (d *Daemon) gatewayMethods(s *rpc.Server, transport string) {
	keep := alwaysNative
	if transport == "network" {
		keep = map[string]bool{}
		for n := range alwaysNative {
			keep[n] = true
		}
		for _, n := range d.cfg.Native {
			keep[n] = true
		}
		relay := d.cfg.RelaySocket
		s.Relay = &rpc.Relay{Dial: func() (net.Conn, error) { return net.DialTimeout("unix", relay, 2*time.Second) }}
	}
	s.Restrict(func(name string) bool { return keep[name] })
}

// pidFile is the gateway's record of itself, which the relay's trust check reads: one decimal pid and a newline.
type pidFile struct {
	path string
	pid  int
}

// takePidFile writes the pid file, 0600, unless a gateway that is alive holds it.
func takePidFile(path string) (*pidFile, error) {
	if b, err := os.ReadFile(path); err == nil {
		if old, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil && old != os.Getpid() && alive(old) {
			return nil, fmt.Errorf("a gateway already runs (pid %d, %s)", old, path)
		}
	}
	if err := os.WriteFile(path, []byte(strconv.Itoa(os.Getpid())+"\n"), 0o600); err != nil {
		return nil, err
	}
	if err := os.Chmod(path, 0o600); err != nil {
		return nil, err
	}
	return &pidFile{path, os.Getpid()}, nil
}

func alive(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

// remove deletes the file if it still names this process.
func (p *pidFile) remove() {
	if p == nil {
		return
	}
	if b, err := os.ReadFile(p.path); err == nil && strings.TrimSpace(string(b)) == strconv.Itoa(p.pid) {
		os.Remove(p.path)
	}
}
