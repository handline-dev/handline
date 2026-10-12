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

	"github.com/handline/handline/go/internal/adminwrites"
	"github.com/handline/handline/go/internal/authority"
	"github.com/handline/handline/go/internal/chatwrites"
	"github.com/handline/handline/go/internal/devices"
	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/hrwrites"
	"github.com/handline/handline/go/internal/lands"
	"github.com/handline/handline/go/internal/legacyverbs"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/sessionrpc"
	"github.com/handline/handline/go/internal/sessiontoken"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/termchan"
	"github.com/handline/handline/go/internal/transcript"
	"github.com/handline/handline/go/internal/version"
	"github.com/handline/handline/go/internal/watcher"
	"github.com/handline/handline/go/internal/weblogin"
)

// daemonLock is the name of the singleton the Python daemon also holds (handline/daemon/locks.py DAEMON_LOCK).
const daemonLock = "daemon"

// structuredLock is the singleton of the process that runs the structured sessions of a home. The agent lock that keeps a session to one
// launch is in-process, so a second process on the home would launch and wake the same agents again.
const structuredLock = "structured"

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
	RelaySocket string   // a gateway: the daemon's relay socket it calls. A daemon: where it serves one, for the gateway; empty serves none
	PidFile     string   // where the gateway records its pid, for the relay's trust check; a daemon with a RelaySocket reads it
	Native      []string // methods served here rather than relayed, besides hello, whoami and the device methods
	WebDir      string   // the built web UI the listener serves on its own origin; empty serves none
	Advertise   string   // the host:port a pairing code names, when it differs from where the listener binds

	// OwnPrompts makes this daemon the one writer of prompt_blocks: it runs the screen watch. Without it the rows are only read.
	// Every daemon but a gateway takes the daemon singleton, so none starts beside a Python daemon.
	OwnPrompts bool
	Terminal   term.Backend // the host the screen watch reads; nil is the configured one (tests give a fake)

	// EnsureFounderKey makes the founder key when there is none and reports whether the store could be written (the daemon main sets it
	// to the home's tokens: the store is outside the home, so a test leaves it nil).
	EnsureFounderKey func() bool

	// LandRepo is the repository main is checked out in, for the land verbs; empty reads OCP_LAND_REPO and refuses a land with none.
	LandRepo string
	// LandSweep makes this daemon merge the lands that are queued: on only where the Python daemon no longer ticks, since two
	// sweeps over one home would race for the same row.
	LandSweep bool

	// Teller follows the task writes with what Python did after their commit (the typed says, the deliveries): the supervisor's telling
	// layer. Nil is the Env's own telling once its ports are all wired (OnOpen sets them), and tells nothing before that.
	Teller taskwrites.Teller

	// Env is the telling layer the chat, mailbox and say writes run on (supervisor.Env). Nil is an Env of this database and home alone: its
	// writes are made, and what needs a port that has not landed is left to the tick.
	Env *supervisor.Env

	// Transcript is where the structured sessions' events go (structured.Manager.Events). With Structured it makes the daemon serve the
	// transcript topic and transcript.events, and say so in hello. Nil serves neither.
	Transcript *transcript.Sink
	// Structured says whether an agent runs as a structured session.
	Structured func(agent string) bool
	// Watch runs the detectors that have moved here from the Python daemon's watcher, which write their rows to the stream. Off while that
	// watcher still runs: a second writer would double every event.
	Watch bool
	// Answers answers the permission prompts of structured agents (prompt.answer). Nil leaves every agent to the screen.
	Answers prompts.Structured
	// SessionOpen says whether a structured agent's session is running (agent.get's session_open). Nil is none.
	SessionOpen func(agent string) bool
	// Context is the model id and context figures of an agent on an account (agent.strip). Nil leaves them null.
	Context func(agent, account string) (string, *int, *int64, int)
	// Delivery is what the supervisor knows of why a queued line waits (a chat line's sending_reason). Nil leaves out the reasons that need it.
	Delivery reads.Delivery
	// Session makes the env of the session methods (session.start, wake, stop, restart, park, interrupt) once the lifecycle is built; nil
	// serves none of them.
	Session func() *sessionrpc.Env
	// OnOpen is given the database once it is open, for the parts built before it was (the structured manager's ledger and prompt rows).
	OnOpen func(db *sql.DB, env *supervisor.Env)
	// Agents is the structured sessions as the supervisor's telling layer sees them (supervisor.Ports.Agents): it types a structured agent
	// through its session. Nil leaves every agent a terminal agent.
	Agents supervisor.Agents
}

// Daemon is a running daemon. Close stops the listeners and waits for the connections to end.
type Daemon struct {
	cfg    Config
	db     *sql.DB
	hooks  *hooks.Recorder
	lands  *lands.Service    // the land verbs; nil on a gateway
	logins *weblogin.Manager // the web logins; nil in a gateway

	acctMu  sync.Mutex        // one account.changed is made and published at a time, so the topic gets a login's moves in order
	acctSeq map[string]uint64 // the number of the last move published for each account
	prompts *prompts.Env      // nil on a gateway: the Python daemon alone presses keys
	tchan   *termchan.Env     // the terminal channel and the input hold; nil on a gateway
	single  *lock.Hold        // the daemon singleton, held while this daemon runs; nil on a gateway
	running *lock.Hold        // the structured singleton, held while this daemon runs structured sessions
	stop    chan struct{}     // closed by Close: the spool loop ends
	started time.Time
	ln      net.Listener
	relayLn net.Listener // the relay socket a gateway calls this daemon on; nil on a gateway or with no RelaySocket
	local   *rpc.Server
	net     *networkListener
	dev     *devices.Manager // nil unless the network listener is on
	pidFile *pidFile
	hub     *stream.Hub
	wg      sync.WaitGroup
	envOnce sync.Once
	envMade *supervisor.Env
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
	if cfg.OnOpen != nil {
		cfg.OnOpen(db, d.env())
	}
	if !cfg.Gateway { // the Python daemon alone writes agent state: a gateway has no hook.record and reads no spool
		d.hooks = &hooks.Recorder{DB: db, Home: cfg.Home, Log: cfg.Log}
		d.logins = &weblogin.Manager{Home: cfg.Home}
		d.prompts = prompts.New(db, cfg.Home, &reads.Env{DB: db, Home: cfg.Home, Started: d.started, Sha: version.Sha, Schema: store.LatestVersion()})
		d.prompts.Structured = cfg.Answers
		if cfg.Terminal != nil {
			d.prompts.Term = term.New(cfg.Terminal, nil)
		}
		d.tchan = termchan.New(db, cfg.Home, func() term.Backend {
			if cfg.Terminal != nil {
				return cfg.Terminal
			}
			return term.Current(cfg.Home)
		})
		d.tchan.Structured = cfg.Structured
		d.ingestSpool() // what hooks left while no daemon ran, before the first call is served
		d.lands = &lands.Service{DB: db, Repo: cfg.LandRepo, Tokens: sessiontoken.New(cfg.Home), Log: cfg.Log,
			Tell: func(to, body string) {
				if err := d.env().SystemSay(lands.Requester, to, body); err != nil {
					cfg.Log("  land: telling %s: %v", to, err)
				}
			}}
	}
	if cfg.Gateway {
		pid, err := takePidFile(cfg.PidFile)
		if err != nil {
			db.Close()
			return nil, err
		}
		d.pidFile = pid
	}
	if !cfg.Gateway {
		if d.single = lock.TakeSingleton(cfg.Home, daemonLock); d.single == nil {
			db.Close()
			return nil, errors.New("another daemon holds this home: not starting a second")
		}
		// Before the socket takes a call: without the founder key it would serve no founder call.
		if cfg.EnsureFounderKey != nil && !cfg.EnsureFounderKey() {
			cfg.Log("founder key: could not make one - the socket will serve no founder call")
		}
	}
	if cfg.Structured != nil {
		if d.running = lock.TakeSingleton(cfg.Home, structuredLock); d.running == nil {
			db.Close()
			d.single.Release()
			return nil, errors.New("another daemon runs the structured sessions of this home: not starting a second")
		}
	}
	if cfg.Listen != "" {
		d.dev = devices.New(devices.SQLStore{DB: db})
		d.dev.Log = cfg.Log
	}
	d.hub = stream.NewHub()
	d.hub.Log = &stream.Log{DB: db}
	d.hub.Reach = (&reads.Env{DB: db, Home: cfg.Home}).Reach
	if d.logins != nil { // a login's state moving is an account.changed on the accounts topic
		d.logins.Changed = d.accountChanged
	}
	if d.transcripts() {
		sink := cfg.Transcript
		sink.DB = db
		sink.Publish = func(e transcript.Event) { d.hub.EmitTranscript(e.Agent, e.JSON()) }
		sink.Prompt = (&reads.Env{DB: db}).PromptOf
		sink.Err = func(agent string, err error) { cfg.Log("  transcript: %s: an event was not kept: %v", agent, err) }
	}
	d.local = d.newServer("local")
	if cfg.SocketFile != "" {
		ln, err := listenUnix(cfg.SocketFile)
		if err != nil {
			db.Close()
			d.pidFile.remove()
			if d.single != nil {
				d.single.Release()
			}
			d.running.Release()
			return nil, err
		}
		d.ln = ln
		d.wg.Add(1)
		go d.acceptLocal(d.local)
	}
	if !cfg.Gateway && cfg.RelaySocket != "" {
		ln, err := listenUnix(cfg.RelaySocket)
		if err != nil {
			d.Close()
			return nil, err
		}
		d.relayLn = ln
		d.wg.Add(1)
		go d.serveRelay(d.newServer("relay"), ln)
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
	if d.lands != nil && cfg.LandSweep {
		d.wg.Add(1)
		go d.landLoop()
	}
	d.wg.Add(1)
	go func() {
		defer d.wg.Done()
		d.hub.Run(d.stop, cfg.Log)
	}()
	if cfg.Watch && !cfg.Gateway {
		w := &watcher.Watcher{DB: db, Emit: d.hub.Append, Log: cfg.Log, Signature: watcher.HRSignature(cfg.Home),
			Detectors: []watcher.Detector{&watcher.Tasks{Reads: &reads.Env{DB: db, Home: cfg.Home}}, &watcher.Agents{}, &watcher.Notifications{Reads: &reads.Env{DB: db, Home: cfg.Home}}, &watcher.Inbox{Reads: &reads.Env{DB: db, Home: cfg.Home}},
				&watcher.HR{Reads: &reads.Env{DB: db, Home: cfg.Home}},
				&watcher.Chat{Reads: &reads.Env{DB: db, Home: cfg.Home, Delivery: cfg.Delivery}}}}
		d.wg.Add(1)
		go func() {
			defer d.wg.Done()
			w.Run(d.stop)
		}()
	}
	if cfg.OwnPrompts && d.prompts != nil {
		d.wg.Add(1)
		go func() {
			defer d.wg.Done()
			d.prompts.Run(d.stop, cfg.Log)
		}()
	}
	cfg.Log("handlined %s serving %q", version.Sha, cfg.SocketFile)
	return d, nil
}

// env is the telling layer the writes run on.
func (d *Daemon) env() *supervisor.Env {
	d.envOnce.Do(func() {
		d.envMade = d.cfg.Env
		if d.envMade == nil {
			d.envMade = &supervisor.Env{DB: d.db, Home: d.cfg.Home, Log: d.cfg.Log}
		}
		if d.envMade.Agents == nil {
			d.envMade.Agents = d.cfg.Agents
		}
	})
	return d.envMade
}

// teller is what follows the task and chat writes: the configured one, else the supervisor Env once every port has its owner, since a
// missing port is a nil call. Nil tells nothing; the tick does it later.
func (d *Daemon) teller() taskwrites.Teller {
	if d.cfg.Teller != nil {
		return d.cfg.Teller
	}
	if e := d.env(); e.Ports.Wired() {
		return e
	}
	return nil
}

// hrEnv is what hr.decide runs on: the telling layer for the proposer, and the process table when the Env has an observer.
func (d *Daemon) hrEnv() *hrwrites.Env {
	env := d.env()
	h := &hrwrites.Env{DB: d.db, Home: d.cfg.Home, Say: env, Log: d.cfg.Log}
	if env.Observer != nil {
		h.Live = env.Observer.LiveSessions
	}
	return h
}

// legacy answers a legacy `{verb}` request: the land verbs, then the rest that Python's hl still sends. nil is a verb served by neither.
func (d *Daemon) legacy(payload map[string]any, pid int) map[string]any {
	if d.lands != nil {
		if reply := d.lands.Legacy(payload, pid); reply != nil {
			return reply
		}
	}
	e := &legacyverbs.Env{DB: d.db, Home: d.cfg.Home, Sup: d.env(), Prompts: d.prompts, SetByAgent: d.hrEnv().SetByAgent, Log: d.cfg.Log}
	if d.cfg.Session != nil {
		e.Controls = func() sessionrpc.Controls { return d.cfg.Session().Controls }
	}
	return e.Legacy(payload, pid)
}

// accountChanged says an account changed (its web login moved) on the accounts topic, as accounts.list shows the account with the login in
// the state the move made, not the one it has by now. Announcements arrive in any order; one numbered before the last published for the
// account is dropped, so the topic never says an earlier state after a later one.
func (d *Daemon) accountChanged(name string, login weblogin.Login) {
	d.acctMu.Lock()
	defer d.acctMu.Unlock()
	if login.Seq <= d.acctSeq[name] {
		return
	}
	acct, ok := (&reads.Env{DB: d.db, Home: d.cfg.Home, Login: func(string) *weblogin.Login { return &login }}).Account(name)
	if !ok {
		return
	}
	if err := d.hub.AccountChanged(acct); err != nil {
		d.cfg.Log("  stream: account.changed %s: %v", name, err)
		return
	}
	if d.acctSeq == nil {
		d.acctSeq = map[string]uint64{}
	}
	d.acctSeq[name] = login.Seq
}

// transcripts says whether this daemon serves the transcript stream: it has the sink that numbers the events and knows which agents are
// structured sessions.
func (d *Daemon) transcripts() bool { return d.cfg.Transcript != nil && d.cfg.Structured != nil }

// writeRegistrars are the packages that register methods once the hooks are wired, one per line in alphabetical order of the package: a port
// adds its line at its own place, so two ports do not edit the same spot and git merges them.
func (d *Daemon) writeRegistrars() []func(*rpc.Server) {
	return []func(*rpc.Server){
		func(s *rpc.Server) {
			adminwrites.Register(s, &adminwrites.Env{DB: d.db, Home: d.cfg.Home, Logins: d.logins})
		},
		func(s *rpc.Server) { chatwrites.Register(s, d.env(), d.teller()) },
		func(s *rpc.Server) { d.hooks.Register(s) },
		func(s *rpc.Server) { hrwrites.Register(s, d.hrEnv()) },
		func(s *rpc.Server) { prompts.Register(s, d.prompts) },
		func(s *rpc.Server) {
			if d.cfg.Session != nil {
				env := d.cfg.Session()
				env.Emit = d.operationEvent
				sessionrpc.Register(s, env)
			}
		},
		func(s *rpc.Server) { taskwrites.Register(s, d.cfg.Home, d.teller()) },
		func(s *rpc.Server) { termchan.Register(s, d.tchan) },
	}
}

// operationEvent sends an operation's progress to the operations topic.
func (d *Daemon) operationEvent(topic, typ string, data any, agent string) {
	if _, err := d.hub.Append(stream.Entry{Topic: topic, Type: typ, Data: data, Agent: agent}); err != nil {
		d.cfg.Log("  stream: %s: %v", typ, err)
	}
}

// newServer is a connection server on one transport, with the methods this daemon serves.
func (d *Daemon) newServer(transport string) *rpc.Server {
	s := rpc.NewServer(d.cfg.Auth, version.Sha, store.LatestVersion(), transport, d.cfg.Log)
	s.DB = d.db
	s.NoEvents = d.cfg.Gateway
	if d.hooks != nil {
		for _, register := range d.writeRegistrars() {
			register(s)
		}
	}
	if transport == "network" {
		s.Net = d.dev
	}
	if transport == "local" {
		s.Legacy = d.legacy
	}
	if d.lands != nil && transport == "local" {
		lands.Register(s, d.lands)
	}
	devices.Register(s, d.dev, d.hub.Emit)
	stream.Register(s, d.hub)
	renv := reads.Env{DB: d.db, Home: d.cfg.Home, Started: d.started, Sha: version.Sha, Schema: store.LatestVersion(), Delivery: d.cfg.Delivery}
	if d.transcripts() {
		renv.Structured, renv.SessionOpen = d.cfg.Structured, d.cfg.SessionOpen
		s.DeclareCapabilities(func() map[string]any { return map[string]any{"transcript": true} })
	}
	renv.Context = d.cfg.Context
	if d.logins != nil {
		renv.Login = d.logins.Of
	}
	reads.Register(s, renv)
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

// landLoop starts the oldest queued land every LandEvery, the work the Python daemon does each tick.
func (d *Daemon) landLoop() {
	defer d.wg.Done()
	t := time.NewTicker(landEvery)
	defer t.Stop()
	for {
		select {
		case <-d.stop:
			return
		case <-t.C:
			if err := d.lands.Sweep(); err != nil {
				d.cfg.Log("  lands: %v", err)
			}
		}
	}
}

// landEvery is how often a queued land is looked for.
const landEvery = 2 * time.Second

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
	if d.relayLn != nil {
		d.relayLn.Close()
		os.Remove(d.cfg.RelaySocket)
	}
	if d.net != nil {
		d.net.close()
	}
	if d.logins != nil {
		d.logins.Close()
	}
	d.wg.Wait()
	d.pidFile.remove()
	if d.single != nil {
		d.single.Release()
	}
	if d.running != nil {
		d.running.Release()
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
