// Package daemon runs the listeners: the local socket, and the optional network listener, one contract on both.
package daemon

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net"
	"os"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/authority"
	"github.com/handline/handline/go/internal/devices"
	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/termchan"
	"github.com/handline/handline/go/internal/version"
)

// daemonLock is the name of the singleton the Python daemon holds (handline/daemon/locks.py DAEMON_LOCK).
const daemonLock = "daemon"

type Config struct {
	Home       string
	DBFile     string
	SocketFile string
	// Listen is the address the network listener binds; empty leaves it off. It is an encrypted
	// WebSocket, except in a gateway's Tailscale mode (see Gateway).
	Listen string
	// CertFile and KeyFile are the listener's certificate; with neither, one is made in memory for this run.
	CertFile, KeyFile string
	Log               func(format string, args ...any)
	Auth              authority.Authority // nil: the protected store of Home

	// Gateway makes this daemon the network front door of a Python daemon that owns Home: it opens the database
	// without changing it, writes nothing but its devices table and its pid file, serves natively only the device
	// methods and Native, and relays every other call a paired device may make to RelaySocket. It needs Listen, and its
	// local socket is never the Python daemon's. With no CertFile and KeyFile it is in Tailscale mode: Listen is
	// ":PORT" (the Tailscale address is found) or a Tailscale or loopback address, and it serves plain HTTP/ws.
	Gateway     bool
	RelaySocket string   // the Python daemon's relay socket
	PidFile     string   // where the gateway records its pid, for the relay's trust check
	Native      []string // methods served here rather than relayed, besides hello, whoami and the device methods
	WebDir      string   // the built web UI the listener serves on its own origin; empty serves none
	Advertise   string   // the host:port a pairing code names, when it differs from where the listener binds

	// OwnPrompts makes this daemon the one writer of prompt_blocks: it runs the screen watch. It takes the daemon singleton the
	// Python daemon holds, so it does not start beside one; without it the rows are the other daemon's and are only read.
	OwnPrompts bool
	Terminal   term.Backend // the host the screen watch reads; nil is the configured one (tests give a fake)

	// Teller follows the task writes with what Python did after their commit (the typed says, the deliveries): the supervisor's telling
	// layer. Nil tells nothing.
	Teller taskwrites.Teller
}

// Daemon is a running daemon. Close stops the listeners and waits for the connections to end.
type Daemon struct {
	cfg     Config
	db      *sql.DB
	hooks   *hooks.Recorder
	prompts *prompts.Env  // nil on a gateway: the Python daemon alone presses keys
	tchan   *termchan.Env // the terminal channel and the input hold; nil on a gateway
	single  *lock.Hold    // the daemon singleton, held while this daemon watches screens
	stop    chan struct{} // closed by Close: the spool loop ends
	started time.Time
	ln      net.Listener
	local   *rpc.Server
	net     *networkListener
	dev     *devices.Manager // nil unless the network listener is on
	pidFile *pidFile
	hub     *stream.Hub
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
	if cfg.plain() && cfg.Listen != "" {
		addr, err := plainAddr(cfg.Listen)
		if err != nil {
			return nil, err
		}
		cfg.Listen = addr
	}
	if cfg.Gateway {
		if err := checkGateway(cfg); err != nil {
			return nil, err
		}
	}
	open := store.Open
	if cfg.Gateway {
		open = store.OpenShared
	}
	db, err := open(cfg.DBFile)
	if err != nil {
		return nil, err
	}
	d := &Daemon{cfg: cfg, db: db, started: time.Now(), conns: map[net.Conn]struct{}{}, stop: make(chan struct{})}
	if !cfg.Gateway { // the Python daemon alone writes agent state: a gateway has no hook.record and reads no spool
		d.hooks = &hooks.Recorder{DB: db, Home: cfg.Home, Log: cfg.Log}
		d.prompts = prompts.New(db, cfg.Home, &reads.Env{DB: db, Home: cfg.Home, Started: d.started, Sha: version.Sha, Schema: store.LatestVersion()})
		if cfg.Terminal != nil {
			d.prompts.Term = term.New(cfg.Terminal, nil)
		}
		d.tchan = termchan.New(db, cfg.Home, func() term.Backend {
			if cfg.Terminal != nil {
				return cfg.Terminal
			}
			return term.Current(cfg.Home)
		})
		d.ingestSpool() // what hooks left while no daemon ran, before the first call is served
	}
	if cfg.Gateway {
		pid, err := takePidFile(cfg.PidFile)
		if err != nil {
			db.Close()
			return nil, err
		}
		d.pidFile = pid
	}
	if cfg.OwnPrompts && d.prompts != nil {
		if d.single = lock.TakeSingleton(cfg.Home, daemonLock); d.single == nil {
			db.Close()
			return nil, errors.New("another daemon holds this home: not starting a second screen watch")
		}
	}
	if cfg.Listen != "" {
		d.dev = devices.New(devices.SQLStore{DB: db})
	}
	d.hub = stream.NewHub()
	d.local = d.newServer("local")
	if cfg.SocketFile != "" {
		ln, err := listenUnix(cfg.SocketFile)
		if err != nil {
			db.Close()
			d.pidFile.remove()
			if d.single != nil {
				d.single.Release()
			}
			return nil, err
		}
		d.ln = ln
		d.wg.Add(1)
		go d.acceptLocal(d.local)
	}
	if cfg.Listen != "" {
		nl, err := listenNetwork(cfg, d)
		if err != nil {
			d.Close()
			return nil, err
		}
		d.net = nl
	}
	if d.hooks != nil {
		d.wg.Add(1)
		go d.spoolLoop()
	}
	if d.single != nil {
		d.wg.Add(1)
		go func() {
			defer d.wg.Done()
			d.prompts.Run(d.stop, cfg.Log)
		}()
	}
	cfg.Log("handlined %s serving %q", version.Sha, cfg.SocketFile)
	return d, nil
}

// newServer is a connection server on one transport, with the methods this daemon serves.
func (d *Daemon) newServer(transport string) *rpc.Server {
	s := rpc.NewServer(d.cfg.Auth, version.Sha, store.LatestVersion(), transport, d.cfg.Log)
	s.DB = d.db
	s.NoEvents = d.cfg.Gateway
	if d.hooks != nil {
		d.hooks.Register(s)
		taskwrites.Register(s, d.cfg.Home, d.cfg.Teller)
		prompts.Register(s, d.prompts)
		termchan.Register(s, d.tchan)
	}
	if transport == "network" {
		s.Net = d.dev
	}
	devices.Register(s, d.dev, d.hub.Emit)
	stream.Register(s, d.hub)
	reads.Register(s, reads.Env{DB: d.db, Home: d.cfg.Home, Started: d.started, Sha: version.Sha, Schema: store.LatestVersion()})
	if d.cfg.Gateway {
		d.gatewayMethods(s, transport)
	}
	return s
}

func (d *Daemon) ingestSpool() {
	if _, err := d.hooks.Ingest(); err != nil {
		d.cfg.Log("  spool: %v", err)
	}
}

// spoolLoop reads the hook spool every SpoolEvery, the work the Python daemon does each tick.
func (d *Daemon) spoolLoop() {
	defer d.wg.Done()
	t := time.NewTicker(hooks.SpoolEvery)
	defer t.Stop()
	for {
		select {
		case <-d.stop:
			return
		case <-t.C:
			d.ingestSpool()
		}
	}
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

// ServeFile serves one connection that already exists, a unix stream socket made by the caller (a socketpair):
// for a run where no socket can be bound. The caller on it is told apart as on the local socket.
func (d *Daemon) ServeFile(f *os.File) error {
	c, err := net.FileConn(f)
	f.Close()
	if err != nil {
		return err
	}
	if !d.track(c) {
		c.Close()
		return errors.New("daemon is closing")
	}
	go func() {
		defer d.untrack(c)
		d.local.Serve(rpc.NewLineTransport(c), peer.PID(c))
	}()
	return nil
}

// ServeNetFile serves one connection that already exists as a network connection: past TLS and the pinned
// fingerprint, so the gate and the device roles apply. It is how the contract's network fixtures reach a daemon where
// no socket can be bound.
func (d *Daemon) ServeNetFile(f *os.File) error {
	c, err := net.FileConn(f)
	f.Close()
	if err != nil {
		return err
	}
	if d.net == nil || !d.track(c) {
		c.Close()
		return errors.New("no network listener")
	}
	go func() {
		defer d.untrack(c)
		d.net.rs.ServeNet(rpc.NewLineTransport(c), "", func() { c.Close() })
	}()
	return nil
}

// Close stops listening, drops every connection and waits for their goroutines.
func (d *Daemon) Close() {
	d.mu.Lock()
	if !d.closing && d.stop != nil {
		close(d.stop)
	}
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
	d.pidFile.remove()
	if d.single != nil {
		d.single.Release()
	}
	if d.db != nil {
		d.db.Close()
	}
}

// Wait blocks until ctx ends, then closes.
func (d *Daemon) Wait(ctx context.Context) {
	<-ctx.Done()
	d.Close()
}
