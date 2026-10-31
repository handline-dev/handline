package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/service"
)

const serviceUsage = "usage: handlined service ensure [--listen <addr> [--advertise <name>]] [<dir or Handline.app>] | install [--allow-live-home] | upgrade [--force] [<dir or Handline.app>] | rollback | status [--json] | stop | uninstall"

// serviceCommand is `handlined service ...` on the home HANDLINE_HOME names (the default home otherwise). install and upgrade take
// the handlined and hl in <dir>, the folder this binary is in by default (the app bundle's).
func serviceCommand(args []string) int {
	s := service.New(paths.Home())
	var msg string
	var err error
	switch {
	case len(args) >= 1 && args[0] == "ensure": // one JSON line, which the app shows
		src, ok := ensureArgs(s, args[1:])
		if !ok {
			fmt.Fprintln(os.Stderr, serviceUsage)
			return 2
		}
		line, code := ensureLine(s.Ensure(src))
		fmt.Println(line)
		return code
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

// ensureArgs takes ensure's --listen and --advertise into s (the gateway's address and name; without them the gateway job keeps its
// own) and gives the source folder, this binary's by default.
func ensureArgs(s *service.Service, args []string) (src string, ok bool) {
	src = ownDir()
	for len(args) > 0 {
		switch {
		case len(args) >= 2 && args[0] == "--listen" && args[1] != "":
			s.Listen, args = args[1], args[2:]
		case len(args) >= 2 && args[0] == "--advertise":
			s.Advertise, args = args[1], args[2:]
		case len(args) == 1 && !strings.HasPrefix(args[0], "-"):
			src, args = args[0], nil
		default:
			return "", false
		}
	}
	return src, s.Advertise == "" || s.Listen != ""
}

func ownDir() string {
	exe, _ := os.Executable()
	return filepath.Dir(paths.Resolved(exe))
}

// ensureLine is what service ensure prints, and its exit code: 1 when the version the app carries is not the one running.
func ensureLine(e service.Ensured) (string, int) {
	b, _ := json.Marshal(e)
	if e.State == "failed" || e.State == "kept" {
		return string(b), 1
	}
	return string(b), 0
}
