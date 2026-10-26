package main

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/service"
)

const serviceUsage = "usage: handlined service install [--allow-live-home] | upgrade [<dir>] | rollback | status | uninstall"

// serviceCommand is `handlined service ...` on the home HANDLINE_HOME names (the default home otherwise). install and upgrade take
// the handlined and hl in <dir>, the folder this binary is in by default (the app bundle's).
func serviceCommand(args []string) int {
	s := service.New(paths.Home())
	var msg string
	var err error
	switch {
	case len(args) == 1 && args[0] == "install":
		msg, err = s.Install(ownDir(), false)
	case len(args) == 2 && args[0] == "install" && args[1] == "--allow-live-home":
		msg, err = s.Install(ownDir(), true)
	case len(args) == 1 && args[0] == "upgrade":
		msg, err = s.Upgrade(ownDir())
	case len(args) == 2 && args[0] == "upgrade":
		msg, err = s.Upgrade(args[1])
	case len(args) == 1 && args[0] == "rollback":
		msg, err = s.Rollback()
	case len(args) == 1 && args[0] == "status":
		msg = s.Status()
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
