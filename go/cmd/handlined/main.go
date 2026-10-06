// Command handlined is the Go daemon: one binary, its version the git sha it was built from.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/handline/handline/go/internal/daemon"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/version"
)

func main() {
	home := flag.String("home", paths.Home(), "the handline home")
	db := flag.String("db", "", "database file (default: <home>/db.sqlite)")
	sock := flag.String("socket", "", "local socket (default: <home>/daemon.sock)")
	listen := flag.String("listen", "", "address for the network listener, an encrypted WebSocket; off when empty")
	cert := flag.String("cert", "", "certificate for the network listener (default: one made in memory)")
	key := flag.String("key", "", "private key for the network listener")
	showVersion := flag.Bool("version", false, "print the git sha this binary was built from")
	flag.Parse()
	if *showVersion {
		fmt.Println(version.Sha)
		return
	}
	cfg := daemon.Config{Home: *home, DBFile: *db, SocketFile: *sock, Listen: *listen, CertFile: *cert, KeyFile: *key, Log: log.Printf}
	if cfg.DBFile == "" {
		cfg.DBFile = paths.DBFile(*home)
	}
	if cfg.SocketFile == "" {
		cfg.SocketFile = paths.SocketFile(*home)
	}
	d, err := daemon.Start(cfg)
	if err != nil {
		log.Fatalf("handlined: %v", err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	d.Wait(ctx)
}
