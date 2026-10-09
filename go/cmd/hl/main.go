// Command hl is the hl CLI in Go. A command not ported yet, or given in a form this port does not parse, runs the Python
// entry with the same argv, so each command moves on its own and hl means the same thing either way.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
)

// command runs a ported command on its arguments; ok false hands the argv to Python unchanged.
type command func(c *hlclient.Client, args []string) (code int, ok bool)

var commands = map[string]command{
	"auto-accept":         autoAccept,
	"close":               closeCmd,
	"dev-mode":            devMode,
	"hook":                hook,
	"idle-background":     idleBackground,
	"idle-timeout":        idleTimeout,
	"ignore-cache-expiry": ignoreCacheExpiry,
	"notice-digest":       noticeDigest,
	"progress":            progress,
	"restart":             restart,
	"say":                 say,
	"task":                task,
}

func main() {
	argv := os.Args[1:]
	if len(argv) > 0 {
		if run := commands[argv[0]]; run != nil {
			c := hlclient.New()
			if why := hlclient.LiveRefusal(c.Home); why != "" {
				fmt.Fprintln(os.Stderr, why)
				os.Exit(1)
			}
			if code, ok := run(c, argv[1:]); ok {
				os.Exit(code)
			}
		}
	}
	fmt.Fprintln(os.Stderr, "hl: "+python(argv).Error())
	os.Exit(1)
}

// python execs the Python entry with argv and returns only when it cannot.
func python(argv []string) error {
	cmd, err := pythonArgv(argv)
	if err != nil {
		return err
	}
	return syscall.Exec(cmd[0], cmd, os.Environ())
}

// pythonArgv is the command line that runs the Python entry with argv. The interpreter is the one bin/hl chose
// ($HANDLINE_HL_PYTHON), else PATH's python3; the entry is bin/_ocp_entry.py beside this binary's go/ folder.
func pythonArgv(argv []string) ([]string, error) {
	exe, err := os.Executable()
	if err != nil {
		return nil, err
	}
	if real, err := filepath.EvalSymlinks(exe); err == nil {
		exe = real
	}
	entry := filepath.Join(filepath.Dir(exe), "..", "bin", "_ocp_entry.py")
	py, _ := paths.Getenv("HL_PYTHON")
	if py == "" {
		py = "python3"
	}
	if py, err = exec.LookPath(py); err != nil {
		return nil, err
	}
	return append([]string{py, entry}, argv...), nil
}

// plain is the arguments when none is an option: an option, or --help, is Python's to parse.
func plain(args []string) bool {
	for _, a := range args {
		if len(a) > 1 && a[0] == '-' {
			return false
		}
	}
	return true
}
