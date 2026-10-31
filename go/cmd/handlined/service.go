package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/service"
)

const serviceUsage = "usage: handlined service ensure [<dir or Handline.app>] | install [--allow-live-home] | upgrade [--force] [<dir or Handline.app>] | rollback | status [--json] | stop | uninstall"

// serviceCommand is `handlined service ...` on the home HANDLINE_HOME names (the default home otherwise). install and upgrade take
// the handlined and hl in <dir>, the folder this binary is in by default (the app bundle's).
func serviceCommand(args []string) int {
	s := service.New(paths.Home())
	var msg string
	var err error
	switch {
	case len(args) >= 1 && len(args) <= 2 && args[0] == "ensure": // one JSON line, which the app shows
		src := ownDir()
		if len(args) == 2 {
			src = args[1]
		}
		e := s.Ensure(src)
		b, _ := json.Marshal(e)
		fmt.Println(string(b))
		if e.State == "failed" || e.State == "kept" {
			return 1
		}
		return 0
	case len(args) == 1 && args[0] == "stop":
		msg, err = s.Stop()
	case len(args) == 1 && args[0] == "install":
		msg, err = s.Install(ownDir(), false)
	case len(args) == 2 && args[0] == "install" && args[1] == "--allow-live-home":
		msg, err = s.Install(ownDir(), true)
	case len(args) >= 1 && args[0] == "upgrade":
		force, rest := false, args[1:]
		if len(rest) > 0 && rest[0] == "--force" {
			force, rest = true, rest[1:]
		}
		if len(rest) > 1 {
			fmt.Fprintln(os.Stderr, serviceUsage)
			return 2
		}
		src := ownDir()
		if len(rest) == 1 {
			src = rest[0]
		}
		msg, err = s.Upgrade(src, force)
	case len(args) == 1 && args[0] == "rollback":
		msg, err = s.Rollback()
	case len(args) == 1 && args[0] == "status":
		msg = s.Status()
	case len(args) == 2 && args[0] == "status" && args[1] == "--json":
		msg = s.StatusJSON()
	case len(args) == 1 && args[0] == "uninstall":
		msg, err = s.Uninstall()
	default:
		fmt.Fprintln(os.Stderr, serviceUsage)
		return 2
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	fmt.Println(msg)
	return 0
}

func ownDir() string {
	exe, _ := os.Executable()
	return filepath.Dir(paths.Resolved(exe))
}
