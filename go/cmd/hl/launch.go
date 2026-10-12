package main

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/claude"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sessiontoken"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/usage"
)

// launch is `hl launch <agent>` (session/launch.exec_agent) once the founder switched it on (`hl go launch on`): build
// the session's command line and become it. A Claude agent under tmux is the Go hl's; another vendor, or Orca, whose
// Runs a launch joins the agent to, is Python's.
func launch(c *hlclient.Client, args []string) (int, bool) {
	before, extra := args, []string(nil)
	for i, a := range args {
		if a == "--" { // what follows is claude's, as argparse reads it
			before, extra = args[:i], args[i+1:]
			break
		}
	}
	p, ok := parse(before, []string{"--account", "--prompt-file", "--why", "--by"}, []string{"--force"}, nil)
	if !ok || len(p.pos) == 0 || !goSwitchOn(c.Home, "go_launch") || term.ConfiguredName(c.Home) != "tmux" {
		return 0, false
	}
	agent := p.pos[0]
	extra = append(p.pos[1:], extra...)
	roster := team.NewRoster(c.Home)
	cfg, err := roster.Read(agent)
	if err != nil {
		return 0, false // Python says what is wrong with it
	}
	if vendor, _, _ := team.Parts(cfg); vendor != "claude" {
		return 0, false
	}
	d, err := store.Open(paths.DBFile(c.Home))
	if err != nil {
		fmt.Fprintf(os.Stderr, "hl launch: %v\n", err)
		return 1, true
	}
	defer d.Close()
	l := launcher(c.Home, d, roster)
	opt := func(k string) string { v, _ := p.opt(k); return v }
	err = l.ExecAgent(agent, extra, p.flags["--force"], opt("--prompt-file"), opt("--account"), opt("--why"), opt("--by"))
	var refused *lifecycle.Refusal
	if errors.As(err, &refused) {
		fmt.Fprintln(os.Stderr, refused.Msg) // Python's SystemExit(message)
		return 1, true
	}
	fmt.Fprintf(os.Stderr, "hl launch: %v\n", err)
	return 1, true
}

// launcher is the Launcher a terminal's own `hl launch` runs: the daemon's (compose.Lifecycle) with the ports a person's
// terminal has that the daemon's structured sessions do not - the vendor's command line, the team's rules, the folder's
// approved MCP servers, and the terminal it runs in.
func launcher(home string, d *sql.DB, roster *team.Roster) *lifecycle.Launcher {
	uenv := &usage.Env{Home: home}
	routing := func() *team.Team { t, _ := team.LoadTeam(home); return t }
	return &lifecycle.Launcher{Q: d, Home: home, Roster: roster, Tokens: sessiontoken.New(home), Ports: lifecycle.LaunchPorts{
		Usage:          &lifecycle.UsageAdapter{Env: uenv, Q: d},
		Accounts:       team.Accounts{Home: home},
		Vendors:        launchVendors{home: home, roster: roster},
		CurrentAccount: func(q db.Q, agent string) string { a, _ := team.CurrentAccount(q, agent); return a },
		AccountReading: func(q db.Q, agent string) (string, string, bool) {
			r, ok, err := team.ReadAccount(d, agent, nil)
			if err != nil || !ok {
				return "", "", false
			}
			return r.Account, r.Source, true
		},
		WaitingFor: func(q db.Q, agent string) bool {
			head, err := db.EffectiveHead(q, agent)
			return err != nil || head != nil // a read that fails says something may be waiting
		},
		TeamRules:      func(agent string) []string { return team.Rules(home, agent, routing()) },
		Routing:        routing,
		McpApproved:    team.NewTrust(home).MCPApproved,
		TerminalHandle: func() string { h, _ := tmuxOwnTerminal(); return h },
		WorktreeID:     func() string { _, wt := tmuxOwnTerminal(); return wt },
		Warn:           func(s string) { fmt.Fprintln(os.Stderr, s) },
		Exec: func(argv0 string, argv, env []string) error {
			d.Close() // the session is not handed the database
			return syscall.Exec(argv0, argv, env)
		},
	}}
}

// launchVendors is lifecycle.VendorsPort with the Claude driver; another vendor is refused, as launch leaves it to Python.
type launchVendors struct {
	home   string
	roster *team.Roster
}

func (v launchVendors) ForAgent(agent string) (lifecycle.Driver, error) {
	if vendor := v.AgentVendor(agent); vendor != "claude" {
		return nil, fmt.Errorf("unknown vendor %q", vendor)
	}
	return claude.Driver{Home: v.home}, nil
}

func (v launchVendors) AgentVendor(agent string) string {
	cfg, err := v.roster.Read(agent)
	if err != nil {
		return team.DefaultVendor
	}
	vendor, _, _ := team.Parts(cfg)
	return vendor
}

func (v launchVendors) AccountVendor(acct string) string { return team.AccountVendor(v.home, acct) }

func (v launchVendors) ConfigDir(acct string) string {
	dir, err := team.AccountConfigDir(v.home, acct)
	if err != nil {
		return ""
	}
	return dir
}

var paneRE = regexp.MustCompile(`^%\d+$`)

// tmuxOwnTerminal is the pane this process runs in and its working folder, only when the pane is on our tmux server: a
// pane id of someone else's tmux means nothing on ours (backends/tmux.own_terminal).
func tmuxOwnTerminal() (string, string) {
	pane, server := os.Getenv("TMUX_PANE"), os.Getenv("TMUX")
	sock, _ := paths.Getenv("TMUX_SOCKET")
	if sock == "" {
		sock = "ocp"
	}
	if first, _, _ := strings.Cut(server, ","); !paneRE.MatchString(pane) || filepath.Base(first) != sock {
		return "", ""
	}
	wd, _ := os.Getwd()
	if real, err := filepath.EvalSymlinks(wd); err == nil {
		wd = real // os.getcwd is the folder itself, not $PWD's spelling of it
	}
	return pane, wd
}
