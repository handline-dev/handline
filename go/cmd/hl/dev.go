package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/handline/handline/go/internal/devinstance"
	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
)

const devUsage = `usage: hl dev on <team> [--worktree PATH] [--listen ADDR] [--fresh]
       hl dev off <team>
       hl dev rebuild <team>
       hl dev status`

// dev is a team's dev instance beside the live one (internal/devinstance). Python has no such command, so a line this
// does not read is answered with the usage here.
func dev(c *hlclient.Client, args []string) (int, bool) {
	agent, _ := paths.Getenv("AGENT")
	return runDev(devinstance.System(c.Home), agent, args, os.Stdout, os.Stderr), true
}

// runDev runs the command as agent (empty or human from a terminal): on, off and rebuild are the founder's or the team's
// manager's, the agent named like the team.
func runDev(e *devinstance.Env, agent string, args []string, out, errOut io.Writer) int {
	p, ok := parse(args, []string{"--worktree", "--listen"}, []string{"--fresh"}, nil)
	verb := ""
	if len(p.pos) > 0 {
		verb = p.pos[0]
	}
	wantTeam := verb != "status"
	_, wt := p.opt("--worktree")
	_, listen := p.opt("--listen")
	if !ok || (wantTeam && len(p.pos) != 2) || (!wantTeam && len(p.pos) != 1) || (verb != "on" && (wt || listen || p.flags["--fresh"])) {
		fmt.Fprintln(errOut, devUsage)
		return 2
	}
	if verb == "status" {
		fmt.Fprintln(out, strings.Join(e.Status(), "\n"))
		return 0
	}
	team := p.pos[1]
	if agent != "" && agent != protected.Human && agent != team {
		fmt.Fprintf(errOut, "error: %s's dev instance is the founder's or %s's to change, not %s's\n", team, team, agent)
		return 1
	}
	var in *devinstance.Instance
	var err error
	switch verb {
	case "on":
		o := devinstance.OnOpts{Fresh: p.flags["--fresh"]}
		o.Worktree, _ = p.opt("--worktree")
		o.Listen, _ = p.opt("--listen")
		in, err = e.On(team, o)
	case "off":
		err = e.Off(team)
	case "rebuild":
		in, err = e.Rebuild(team)
	default:
		fmt.Fprintln(errOut, devUsage)
		return 2
	}
	if err != nil {
		fmt.Fprintf(errOut, "error: %v\n", err)
		return 1
	}
	if in == nil {
		fmt.Fprintf(out, "%s's dev instance is off\n", team)
		return 0
	}
	b, _ := json.MarshalIndent(in, "", "  ")
	fmt.Fprintln(out, string(b))
	return 0
}
