// Command handlined is the Go daemon: one binary, its version the git sha it was built from.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"

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
	cfg := daemon.Config{Home: homeDir, DBFile: *db, SocketFile: *sock, Listen: *listen, CertFile: *cert, KeyFile: *key, Log: log.Printf}
	if cfg.DBFile == "" {
		cfg.DBFile = paths.DBFile(homeDir)
	}
	if cfg.SocketFile == "" && *serveFDs == "" {
		cfg.SocketFile = paths.SocketFile(homeDir)
	}
	var fds []int
	for _, f := range strings.FieldsFunc(*serveFDs, func(r rune) bool { return r == ',' }) {
		n, err := strconv.Atoi(f)
		if err != nil {
			log.Fatalf("handlined: -serve-fds: %q is not a descriptor", f)
		}
		fds = append(fds, n)
	}
	d, err := daemon.Start(cfg)
	if err != nil {
		log.Fatalf("handlined: %v", err)
	}
	for _, fd := range fds {
		if err := d.ServeFile(os.NewFile(uintptr(fd), "inherited")); err != nil {
			log.Fatalf("handlined: fd %d: %v", fd, err)
		}
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	d.Wait(ctx)
}
