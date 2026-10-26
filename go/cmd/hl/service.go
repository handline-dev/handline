package main

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
	svc "github.com/handline/handline/go/internal/service"
)

// service is `hl service install | uninstall | status`: the launchd job that keeps the daemon running, installed from the binaries
// beside this one (<checkout>/go).
func service(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 {
		return 0, false
	}
	s := svc.New(c.Home)
	switch args[0] {
	case "install":
		p, ok := parse(args[1:], nil, []string{"--allow-live-home"}, nil)
		if !ok || len(p.pos) != 0 {
			return 0, false
		}
		return finish(s.Install(binDir(), p.flags["--allow-live-home"])), true
	case "uninstall":
		if len(args) != 1 {
			return 0, false
		}
		return finish(s.Uninstall()), true
	case "status":
		if len(args) != 1 {
			return 0, false
		}
		fmt.Println(s.Status())
		return 0, true
	}
	return 0, false
}

func finish(msg string, err error) int {
	if err != nil {
		fmt.Println(err)
		return 1
	}
	fmt.Println(msg)
	return 0
}

// binDir is the folder this binary lives in, links followed.
func binDir() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	return filepath.Dir(paths.Resolved(exe))
}
