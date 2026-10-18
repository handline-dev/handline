// Command hl is the hl CLI. A command it does not have, or a form it does not parse, is refused with exit 2.
package main

import (
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
)

// command runs a command on its arguments; ok false is a form it does not take.
type command func(c *hlclient.Client, args []string) (code int, ok bool)

var commands = map[string]command{
	"account":             account,
	"cancel":              cancel,
	"device":              device,
	"auto-accept":         autoAccept,
	"close":               closeCmd,
	"dev":                 dev,
	"dev-mode":            devMode,
	"hook":                hook,
	"idle-background":     idleBackground,
	"hr":                  hr,
	"idle-timeout":        idleTimeout,
	"init":                initCmd,
	"inbox":               inbox,
	"ignore-cache-expiry": ignoreCacheExpiry,
	"launch":              launch,
	"notice-digest":       noticeDigest,
	"progress":            progress,
	"read":                read,
	"restart":             restart,
	"sandbox-repo":        sandboxRepo,
	"say":                 say,
	"service":             service,
	"task":                task,
	"team":                teamCmd,
	"withdraw":            withdraw,
}

func main() {
	argv := os.Args[1:]
	if len(argv) == 0 || argv[0] == "-h" || argv[0] == "--help" {
		fmt.Fprintf(os.Stderr, "usage: hl <command> ...\ncommands: %s\n", strings.Join(names(), ", "))
		os.Exit(2)
	}
	run := commands[argv[0]]
	if run == nil {
		fmt.Fprintf(os.Stderr, "hl: unknown command %q\ncommands: %s\n", argv[0], strings.Join(names(), ", "))
		os.Exit(2)
	}
	c := hlclient.New()
	if why := hlclient.LiveRefusal(c.Home); why != "" {
		fmt.Fprintln(os.Stderr, why)
		os.Exit(1)
	}
	if code, ok := run(c, argv[1:]); ok {
		os.Exit(code)
	}
	fmt.Fprintf(os.Stderr, "hl %s: arguments not understood: %s\nSee the skill for the form each verb takes.\n", argv[0], strings.Join(argv[1:], " "))
	os.Exit(2)
}

func names() []string {
	var out []string
	for n := range commands {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

// plain is the arguments when none is an option.
func plain(args []string) bool {
	for _, a := range args {
		if len(a) > 1 && a[0] == '-' {
			return false
		}
	}
	return true
}
