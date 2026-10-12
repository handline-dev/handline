// Command handlined is the Go daemon: one binary, its version the git sha it was built from.
package main

import (
	"context"
	"database/sql"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/authority"
	"github.com/handline/handline/go/internal/compose"
	"github.com/handline/handline/go/internal/daemon"
	"github.com/handline/handline/go/internal/housekeeping"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sessionrpc"
	"github.com/handline/handline/go/internal/sessiontoken"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/version"
)

// resolveHome refuses the default home unless it was asked for: this daemon does not yet guard the real
// home against a build that is not the installed one, so nothing starts on it by accident. -home naming the
// real home is refused the same way, and HANDLINE_HOME and OCP_HOME naming different homes is an error.
func resolveHome(flagHome string, allowReal bool) (string, error) {
	newHome, oldHome := os.Getenv("HANDLINE_HOME"), os.Getenv("OCP_HOME")
	if newHome != "" && oldHome != "" && realPath(newHome) != realPath(oldHome) {
		return "", fmt.Errorf("HANDLINE_HOME=%s and OCP_HOME=%s name different homes: set one, or both to the same folder", newHome, oldHome)
	}
	if flagHome != "" {
		if !allowReal && isRealHome(flagHome) {
			return "", fmt.Errorf("-home %s is the real handline home (-allow-real-home starts on it)", flagHome)
		}
		return flagHome, nil
	}
	if !allowReal {
		return "", fmt.Errorf("-home is required while the daemon is in development (-allow-real-home uses %s)", paths.Home())
	}
	return paths.Home(), nil
}

// realPath is p with links followed, through its nearest ancestor that exists.
func realPath(p string) string {
	p = filepath.Clean(p)
	for rest := ""; ; {
		if r, err := filepath.EvalSymlinks(p); err == nil {
			return filepath.Join(r, rest)
		}
		parent := filepath.Dir(p)
		if parent == p {
			return filepath.Join(p, rest)
		}
		rest, p = filepath.Join(filepath.Base(p), rest), parent
	}
}

// isRealHome is whether p is, or is inside, the user's ~/.handline or ~/.orcapipe, by the account's home directory and not the
// test root's stand-in for it.
func isRealHome(p string) bool {
	u, err := os.UserHomeDir()
	if err != nil {
		return false
	}
	p = realPath(p)
	for _, d := range []string{".handline", ".orcapipe"} {
		real := realPath(filepath.Join(u, d))
		if p == real || strings.HasPrefix(p, real+string(filepath.Separator)) {
			return true
		}
	}
	return false
}

// defaultNative is what the Go daemon serves from the database today; every other method is relayed until it is named here. One name per
// line in alphabetical order, so a port that adds one merges with the others.
var defaultNative = strings.Join([]string{
	"accounts.list",
	"agent.get",
	"agent.options",
	"agent.timeline",
	"agents.list",
	"answers.list",
	"chat.locate",
	"chat.messages",
	"chat.requests",
	"chat.search",
	"chat.transcript",
	"daemon.status",
	"events.feed",
	"inbox.get",
	"inbox.list",
	"prompt.list",
	"repos.list",
	"schedules.list",
	"settings.get",
	"stats.get",
	"stats.outlook",
	"status.pick",
	"task.get",
	"tasks.list",
	"team.get",
	"transcript.events",
	"worktree.diff",
	"worktrees.list",
}, ",")

func main() {
	home := flag.String("home", "", "the handline home (required while the daemon is in development)")
	allowReal := flag.Bool("allow-real-home", false, "with no -home, use the default home, which a running install uses")
	db := flag.String("db", "", "database file (default: <home>/db.sqlite)")
	sock := flag.String("socket", "", "local socket (default: <home>/daemon.sock)")
	listen := flag.String("listen", "", "address for the network listener; off when empty. With -gateway and no -cert, :PORT binds the Tailscale address")
	cert := flag.String("cert", "", "certificate for the network listener (default: one made in memory; a gateway with none serves plain HTTP on Tailscale)")
	key := flag.String("key", "", "private key for the network listener")
	serveFDs := flag.String("serve-fds", "", "comma-separated file descriptors of connections already open (socketpairs "+
		"made by the parent, for a run where no socket can be bound); each is served and no socket file is made")
	gateway := flag.Bool("gateway", false, "run as the network front door of the Python daemon that owns the home: needs -listen (with -cert and -key for TLS, without them plain HTTP on the Tailscale address); "+
		"opens the database without changing it, writes only the devices table and the pid file, relays what it does not serve")
	relaySock := flag.String("relay-socket", "", "the Python daemon's relay socket, for -gateway (default: <home>/relay.sock)")
	pidFile := flag.String("pid-file", "", "where -gateway records its pid (default: the founder's protected store, gateway/pid, which no agent sandbox can write)")
	native := flag.String("native", defaultNative, "with -gateway: the methods served here rather than relayed, besides hello, whoami and the device methods")
	web := flag.String("web", "", "the built web UI to serve on the listener's own origin; empty serves none")
	advertise := flag.String("advertise", "", "the host:port a pairing code names, when it differs from -listen (a Tailscale host name)")
	serveNetFDs := flag.String("serve-net-fds", "", "like -serve-fds, but each connection is served as one of the network listener's "+
		"(past TLS), so it is unpaired until a device token says otherwise; needs -listen")
	ownPrompts := flag.Bool("own-prompts", false, "run the screen watch that writes prompt_blocks: this daemon is then the only writer, so it takes the daemon singleton and does not start beside a Python daemon, and is refused with -gateway")
	structuredCLI := flag.String("structured-cli", "", "the claude binary structured sessions run: with it the daemon builds the structured manager and serves the transcript for the agents whose agent.yaml says structured: true; empty runs none")
	claudeVersion := flag.String("claude-version", "", "with -structured-cli: the claude version a structured start accepts instead of the one the control types were read against ("+structured.PinnedCLI+"); a newer claude is run on trust until the types are read against it")
	structuredTick := flag.Bool("structured-tick", false, "with -structured-cli: run the policy tick over the structured agents (it wakes, closes and restarts their sessions); off, the sessions are driven by the session methods only. The tick also reads every account's usage and token log, so the Python daemon must not tick beside it on the same home")
	cutover := flag.Bool("cutover", false, "with -structured-tick: the Python daemon no longer ticks or watches, so this tick owns the usage poll and every agent_view row, and the stream rows of the topics whose detector is here; off, it writes the rows of structured agents only and leaves the usage tables to Python")
	landRepo := flag.String("land-repo", "", "the repository main is checked out in, which `hl land` merges into (default: OCP_LAND_REPO; with neither a land is refused)")
	terminalTick := flag.Bool("terminal-tick", false, "run the policy tick over the terminal agents too, reading their sessions from <config dir>/sessions/<pid>.json (it wakes, closes, restarts and hands off their sessions); needs -structured-cli, and the Python daemon must not tick beside it on the same home")
	showVersion := flag.Bool("version", false, "print the git sha this binary was built from")
	flag.Parse()
	if *showVersion {
		fmt.Println(version.Sha)
		return
	}
	homeDir, err := resolveHome(*home, *allowReal)
	if err != nil {
		log.Fatalf("handlined: %v", err)
	}
	cfg := daemon.Config{Home: homeDir, DBFile: *db, SocketFile: *sock, Listen: *listen, CertFile: *cert, KeyFile: *key, Log: log.Printf,
		Gateway: *gateway, RelaySocket: *relaySock, PidFile: *pidFile, WebDir: *web, Advertise: *advertise, OwnPrompts: *ownPrompts, EnsureFounderKey: sessiontoken.New(homeDir).EnsureFounderKey,
		LandRepo: *landRepo, LandSweep: *structuredTick && *cutover}
	var tk *compose.Tick                                     // the policy tick, once built
	var startTick func(stop <-chan struct{}) <-chan struct{} // set when the policy tick is on: runs it until stop closes, answers when it has returned
	if *structuredCLI != "" && !*gateway {
		ccfg := compose.Config{Home: homeDir, Log: log.Printf, CLI: *structuredCLI, CLIVersion: *claudeVersion, Args: []string{"--setting-sources", "project"}, Policy: compose.SharedPolicy(homeDir)}
		parts := compose.New(ccfg)
		cfg.Watch = *structuredTick && *cutover
		cfg.Delivery = parts.Delivery()
		cfg.Transcript, cfg.Structured, cfg.SessionOpen, cfg.Agents, cfg.Answers = parts.Sink, parts.Kind, parts.Run.Alive, parts.Agents, parts.Manager
		cfg.OnOpen = func(db *sql.DB, env *supervisor.Env) {
			parts.Bind(db, env)
			ccfg.DB = db
			env.Ports = parts.SupervisorPorts(parts.Lifecycle(ccfg, compose.LifecycleConfig{Env: env}))
			if *structuredTick || *terminalTick {
				startTick = func(stop <-chan struct{}) <-chan struct{} {
					tk = parts.Tick(ccfg, compose.TickConfig{Stop: stop, Cutover: *cutover, Terminals: *terminalTick, ScratchRoot: housekeeping.ScratchRoot()})
					return tk.Start()
				}
			}
		}
		cfg.Session = func() *sessionrpc.Env { return parts.SessionRPC(ccfg) }
		cfg.Context = parts.Context
	}
	if *gateway {
		for _, n := range strings.Split(*native, ",") {
			if n = strings.TrimSpace(n); n != "" {
				cfg.Native = append(cfg.Native, n)
			}
		}
		if cfg.RelaySocket == "" {
			cfg.RelaySocket = filepath.Join(homeDir, "relay.sock")
		}
		if cfg.PidFile == "" {
			cfg.PidFile = filepath.Join(authority.Root(homeDir), "gateway", "pid")
		}
		if cfg.SocketFile == "" && *serveFDs == "" && *serveNetFDs == "" {
			cfg.SocketFile = filepath.Join(homeDir, "gateway.sock")
		}
	}
	if !*gateway && *serveFDs == "" && *serveNetFDs == "" {
		if cfg.RelaySocket == "" {
			cfg.RelaySocket = paths.RelaySocket(homeDir)
		}
		if cfg.PidFile == "" {
			cfg.PidFile = filepath.Join(authority.Root(homeDir), "gateway", "pid")
		}
	}
	if cfg.DBFile == "" {
		cfg.DBFile = paths.DBFile(homeDir)
	}
	if cfg.SocketFile == "" && *serveFDs == "" && *serveNetFDs == "" {
		cfg.SocketFile = paths.SocketFile(homeDir)
	}
	descriptors := func(flagName, list string) []int {
		var fds []int
		for _, f := range strings.FieldsFunc(list, func(r rune) bool { return r == ',' }) {
			n, err := strconv.Atoi(f)
			if err != nil {
				log.Fatalf("handlined: -%s: %q is not a descriptor", flagName, f)
			}
			fds = append(fds, n)
		}
		return fds
	}
	fds, netFDs := descriptors("serve-fds", *serveFDs), descriptors("serve-net-fds", *serveNetFDs)
	d, err := daemon.Start(cfg)
	if err != nil {
		log.Fatalf("handlined: %v", err)
	}
	for _, fd := range fds {
		if err := d.ServeFile(os.NewFile(uintptr(fd), "inherited")); err != nil {
			log.Fatalf("handlined: fd %d: %v", fd, err)
		}
	}
	for _, fd := range netFDs {
		if err := d.ServeNetFile(os.NewFile(uintptr(fd), "inherited")); err != nil {
			log.Fatalf("handlined: fd %d: %v", fd, err)
		}
	}
	defer housekeeping.DumpStacksOnUSR1()() // kill -USR1 <pid>: every goroutine's stack, in the err log
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var tickDone <-chan struct{}
	if startTick != nil {
		tickDone = startTick(ctx.Done())
	}
	select {
	case <-ctx.Done():
	case <-tickDone: // the loop ended by itself: dev mode found a changed executable (nil when there is no tick)
	}
	if tickDone != nil {
		<-tickDone // the tick in hand finishes before the database closes: a wake or a close is not cut off in the middle
	}
	d.Close()
	if tk != nil && tk.Restarting() && ctx.Err() == nil { // the lock and the database are given up, as a clean exit gives them up
		if err := tk.Restart(); err != nil {
			log.Fatalf("handlined: dev-mode restart: %v", err)
		}
	}
}
