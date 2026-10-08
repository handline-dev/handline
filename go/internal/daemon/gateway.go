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

	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/rpc"
)

// alwaysNative are the methods a gateway serves itself whatever Native says: the handshake, and the devices, which
// are its own. subscribe and unsubscribe are served here for the devices topic and relayed for every other.
var alwaysNative = map[string]bool{"hello": true, "whoami": true, "device.pair": true, "device.redeem": true,
	"device.revoke": true, "devices.list": true, "subscribe": true, "unsubscribe": true}

func checkGateway(cfg Config) error {
	switch {
	case cfg.Listen == "":
		return errors.New("a gateway needs an address to listen on (-listen)")
	case (cfg.CertFile == "") != (cfg.KeyFile == ""):
		return errors.New("a gateway takes -cert and -key together, or neither (Tailscale mode, plain HTTP)")
	case cfg.OwnPrompts:
		return errors.New("a gateway writes no prompt_blocks: -own-prompts is for a daemon that owns the home")
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
		relay, lock := d.cfg.RelaySocket, filepath.Join(d.cfg.Home, "locks", "daemon.lock")
		s.Relay = &rpc.Relay{Dial: func() (net.Conn, error) {
			c, err := net.DialTimeout("unix", relay, 2*time.Second)
			if err != nil {
				return nil, err
			}
			if err := relayPeerIsTheDaemon(c, lock, peer.PID, peer.Started); err != nil {
				c.Close()
				return nil, err
			}
			return c, nil
		}}
		s.Relay.Capabilities = s.Relay.TerminalCapabilities
	}
	s.Restrict(func(name string) bool { return keep[name] })
}

// relayPeerIsTheDaemon refuses a relay socket nobody but the Python daemon answers on: anyone can unlink and rebind
// a path in the home, so what sits at it must be the process that holds the daemon's lock (the pid it wrote there)
// and have started before the lock was last written, before any device traffic is sent.
func relayPeerIsTheDaemon(c net.Conn, lockFile string, pidOf func(net.Conn) int, startOf func(int) (time.Time, bool)) error {
	b, err := os.ReadFile(lockFile)
	if err != nil {
		return fmt.Errorf("the daemon's lock file: %w", err)
	}
	st, err := os.Stat(lockFile)
	if err != nil {
		return fmt.Errorf("the daemon's lock file: %w", err)
	}
	want, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || want <= 0 {
		return errors.New("the daemon's lock file names no process")
	}
	if got := pidOf(c); got != want {
		return fmt.Errorf("the relay socket is answered by pid %d, not the daemon (pid %d)", got, want)
	}
	// A pid alone goes stale: a crashed daemon's pid, taken by another process, started after the lock was written.
	if started, ok := startOf(want); !ok || started.After(st.ModTime()) {
		return fmt.Errorf("pid %d did not start before the daemon's lock file was written: a reused pid", want)
	}
	return nil
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
	// Written whole under another name and renamed, so the relay never reads half a pid.
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".pid-")
	if err != nil {
		return nil, err
	}
	_, werr := tmp.WriteString(strconv.Itoa(os.Getpid()) + "\n")
	if cerr := tmp.Close(); werr == nil {
		werr = cerr
	}
	if werr == nil {
		werr = os.Chmod(tmp.Name(), 0o600)
	}
	if werr == nil {
		werr = os.Rename(tmp.Name(), path)
	}
	if werr != nil {
		os.Remove(tmp.Name())
		return nil, werr
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
