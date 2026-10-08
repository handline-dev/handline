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
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sessionrpc"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/version"
)

// resolveHome refuses the default home unless it was asked for: this daemon does not yet guard the real
// home against a build that is not the installed one, so nothing starts on it by accident.
func resolveHome(flagHome string, allowReal bool) (string, error) {
	if flagHome != "" {
		return flagHome, nil
	}
	if !allowReal {
		return "", fmt.Errorf("-home is required while the daemon is in development (-allow-real-home uses %s)", paths.Home())
	}
	return paths.Home(), nil
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
	structuredCLI := flag.String("structured-cli", "", "the claude binary structured sessions run: with it the daemon builds the structured manager and serves the transcript for the agents whose agent.yaml says mode: structured; empty runs none")
	structuredTick := flag.Bool("structured-tick", false, "with -structured-cli: run the policy tick over the structured agents (it wakes, closes and restarts their sessions); off, the sessions are driven by the session methods only")
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
		Gateway: *gateway, RelaySocket: *relaySock, PidFile: *pidFile, WebDir: *web, Advertise: *advertise, OwnPrompts: *ownPrompts}
	var startTick func(stop <-chan struct{}) <-chan struct{} // set when the policy tick is on: runs it until stop closes, answers when it has returned
	if *structuredCLI != "" && !*gateway {
		ccfg := compose.Config{Home: homeDir, Log: log.Printf, CLI: *structuredCLI, Args: []string{"--setting-sources", "project"}}
		parts := compose.New(ccfg)
		cfg.Transcript, cfg.Structured, cfg.Agents = parts.Sink, parts.Kind, parts.Agents
		cfg.OnOpen = func(db *sql.DB, env *supervisor.Env) {
			parts.Bind(db, env)
			ccfg.DB = db
			env.Ports = parts.SupervisorPorts(parts.Lifecycle(ccfg, compose.LifecycleConfig{Env: env}))
			if *structuredTick {
				startTick = func(stop <-chan struct{}) <-chan struct{} {
					return parts.Tick(ccfg, compose.TickConfig{Stop: stop}).Start()
				}
			}
		}
		cfg.Session = func() *sessionrpc.Env { return parts.SessionRPC(ccfg) }
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
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var tickDone <-chan struct{}
	if startTick != nil {
		tickDone = startTick(ctx.Done())
	}
	<-ctx.Done()
	if tickDone != nil {
		<-tickDone // the tick in hand finishes before the database closes: a wake or a close is not cut off in the middle
	}
	d.Close()
}
