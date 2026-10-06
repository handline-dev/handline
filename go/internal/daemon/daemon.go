// Package daemon runs the listeners: the local socket, and the optional network listener, one contract on both.
package daemon

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/authority"
	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/version"
)

type Config struct {
	Home       string
	DBFile     string
	SocketFile string
	// Listen is the address the network listener binds; empty leaves it off. It is always an encrypted
	// WebSocket, and a Tailscale or LAN address is only the address it binds.
	Listen string
	// CertFile and KeyFile are the listener's certificate; with neither, one is made in memory for this run.
	CertFile, KeyFile string
	Log               func(format string, args ...any)
	Auth              authority.Authority // nil: the protected store of Home
}

// Daemon is a running daemon. Close stops the listeners and waits for the connections to end.
type Daemon struct {
	cfg     Config
	ln      net.Listener
	net     *networkListener
	wg      sync.WaitGroup
	mu      sync.Mutex
	conns   map[net.Conn]struct{}
	closing bool
}

func Start(cfg Config) (*Daemon, error) {
	if cfg.Log == nil {
		cfg.Log = func(string, ...any) {}
	}
	if cfg.Auth == nil {
		cfg.Auth = authority.New(cfg.Home)
	}
	db, err := store.Open(cfg.DBFile)
	if err != nil {
		return nil, err
	}
	defer db.Close() // the schema is up; the first method that needs the database opens it itself
	ln, err := listenUnix(cfg.SocketFile)
	if err != nil {
		return nil, err
	}
	d := &Daemon{cfg: cfg, ln: ln, conns: map[net.Conn]struct{}{}}
	srv := rpc.NewServer(cfg.Auth, version.Sha, store.LatestVersion(), "local", cfg.Log)
	d.wg.Add(1)
	go d.acceptLocal(srv)
	if cfg.Listen != "" {
		nl, err := listenNetwork(cfg, d)
		if err != nil {
			d.Close()
			return nil, err
		}
		d.net = nl
	}
	cfg.Log("handlined %s serving %s", version.Sha, cfg.SocketFile)
	return d, nil
}

// listenUnix binds the socket, 0600, replacing a stale file but never one a live daemon answers on.
func listenUnix(file string) (net.Listener, error) {
	if c, err := net.DialTimeout("unix", file, time.Second); err == nil {
		c.Close()
		return nil, fmt.Errorf("a daemon already answers on %s", file)
	}
	os.Remove(file)
	old := umaskSet(0o177)
	defer umaskSet(old)
	ln, err := net.Listen("unix", file)
	if err != nil {
		return nil, err
	}
	if err := os.Chmod(file, 0o600); err != nil {
		ln.Close()
		return nil, err
	}
	return ln, nil
}

func (d *Daemon) track(c net.Conn) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.closing {
		return false
	}
	d.conns[c] = struct{}{}
	d.wg.Add(1)
	return true
}

func (d *Daemon) untrack(c net.Conn) {
	d.mu.Lock()
	delete(d.conns, c)
	d.mu.Unlock()
	c.Close()
	d.wg.Done()
}

func (d *Daemon) acceptLocal(srv *rpc.Server) {
	defer d.wg.Done()
	for {
		c, err := d.ln.Accept()
		if err != nil {
			if !errors.Is(err, net.ErrClosed) {
				d.cfg.Log("accept: %v", err)
			}
			return
		}
		if !d.track(c) {
			c.Close()
			return
		}
		go func() {
			defer d.untrack(c)
			srv.Serve(rpc.NewLineTransport(c), peer.PID(c))
		}()
	}
}

// Close stops listening, drops every connection and waits for their goroutines.
func (d *Daemon) Close() {
	d.mu.Lock()
	d.closing = true
	for c := range d.conns {
		c.Close()
	}
	d.mu.Unlock()
	if d.ln != nil {
		d.ln.Close()
		os.Remove(d.cfg.SocketFile)
	}
	if d.net != nil {
		d.net.close()
	}
	d.wg.Wait()
}

// Wait blocks until ctx ends, then closes.
func (d *Daemon) Wait(ctx context.Context) {
	<-ctx.Done()
	d.Close()
}
