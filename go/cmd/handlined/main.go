// Command handlined is the Go daemon: one binary, its version the git sha it was built from.
package main

import (
	"context"
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
	"github.com/handline/handline/go/internal/daemon"
	"github.com/handline/handline/go/internal/paths"
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

// defaultNative is what the Go daemon serves from the database today; every other method is relayed until it is named here.
const defaultNative = "daemon.status,agents.list,agent.get,agent.timeline,tasks.list,task.get,inbox.list"

func main() {
	home := flag.String("home", "", "the handline home (required while the daemon is in development)")
	allowReal := flag.Bool("allow-real-home", false, "with no -home, use the default home, which a running install uses")
	db := flag.String("db", "", "database file (default: <home>/db.sqlite)")
	sock := flag.String("socket", "", "local socket (default: <home>/daemon.sock)")
	listen := flag.String("listen", "", "address for the network listener, an encrypted WebSocket; off when empty")
	cert := flag.String("cert", "", "certificate for the network listener (default: one made in memory)")
	key := flag.String("key", "", "private key for the network listener")
	serveFDs := flag.String("serve-fds", "", "comma-separated file descriptors of connections already open (socketpairs "+
		"made by the parent, for a run where no socket can be bound); each is served and no socket file is made")
	gateway := flag.Bool("gateway", false, "run as the network front door of the Python daemon that owns the home: needs -listen, -cert and -key; "+
		"opens the database without changing it, writes only the devices table and the pid file, relays what it does not serve")
	relaySock := flag.String("relay-socket", "", "the Python daemon's relay socket, for -gateway (default: <home>/relay.sock)")
	pidFile := flag.String("pid-file", "", "where -gateway records its pid (default: the founder's protected store, gateway/pid, which no agent sandbox can write)")
	native := flag.String("native", defaultNative, "with -gateway: the methods served here rather than relayed, besides hello, whoami and the device methods")
	web := flag.String("web", "", "the built web UI to serve on the listener's own origin; empty serves none")
	advertise := flag.String("advertise", "", "the host:port a pairing code names, when it differs from -listen (a Tailscale host name)")
	serveNetFDs := flag.String("serve-net-fds", "", "like -serve-fds, but each connection is served as one of the network listener's "+
		"(past TLS), so it is unpaired until a device token says otherwise; needs -listen")
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
		Gateway: *gateway, RelaySocket: *relaySock, PidFile: *pidFile, WebDir: *web, Advertise: *advertise}
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
	d.Wait(ctx)
}
