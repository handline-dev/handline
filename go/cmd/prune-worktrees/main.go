// Command prune-worktrees lists the worktrees of the repository it runs in that are done with, and removes them with --remove
// (tools/prune_worktrees.py runs it). The rule is worktreeprune's, the same the daemon uses when a member leaves the team.
package main

import (
	"database/sql"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/worktreeprune"
	_ "modernc.org/sqlite"
)

func main() { os.Exit(run()) }

func run() int {
	home := flag.String("home", "", "the home whose agents' worktrees stay")
	remove := flag.Bool("remove", false, "remove them; without it nothing is changed")
	flag.Parse()
	if *home == "" {
		*home = paths.Home()
	}
	d, err := sql.Open("sqlite", "file:"+paths.DBFile(*home)+"?mode=ro")
	var agents map[string]bool
	if err == nil {
		agents, err = worktreeprune.AgentWorktrees(d, "")
		d.Close()
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot read the agents' worktrees from %s: %v; nothing is removed\n", *home, err)
		return 1
	}
	cwd, _ := os.Getwd()
	here := strings.TrimSpace(gitrun.Run(cwd, "rev-parse", "--show-toplevel").Stdout)
	all := worktreeprune.List(cwd)
	if all == nil {
		fmt.Fprintln(os.Stderr, "git cannot list the worktrees here; nothing is removed")
		return 1
	}
	failed := 0
	for _, wt := range all[1:] { // the first is the repository's own checkout
		switch why := worktreeprune.WhyKept(wt, agents, here); {
		case why != "":
			fmt.Printf("keep    %s: %s\n", wt.Path, why)
		case !*remove:
			fmt.Printf("remove  %s\n", wt.Path)
		default:
			if ok, why := worktreeprune.Remove(wt.Path); ok {
				fmt.Printf("removed %s\n", wt.Path)
			} else {
				fmt.Printf("FAILED  %s: %s\n", wt.Path, why)
				failed++
			}
		}
	}
	if !*remove {
		fmt.Fprintln(os.Stderr, "nothing is changed: --remove removes the ones marked remove")
	}
	if failed > 0 {
		return 1
	}
	return 0
}
