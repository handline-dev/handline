package main

import (
	"fmt"
	"os"
	"slices"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/team"
)

const sandboxRepoUsage = "usage: hl sandbox-repo [--clear] repo [allowance ...]"

// sandboxRepo is `hl sandbox-repo <repo> [allowance ...] [--clear]` (cli/hooks.py cmd_sandbox_repo): show or set what
// the founder allows a repo's sandboxed sessions beyond the sandbox (team.RepoAllowancesOf); a session launched after
// this takes it.
func sandboxRepo(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, []string{"--clear"}, nil)
	if !ok || len(p.pos) == 0 {
		return badArgs(sandboxRepoUsage, args), true
	}
	repo, given := p.pos[0], p.pos[1:]
	clear := p.flags["--clear"]
	names := make([]string, 0, len(team.RepoAllowanceNames))
	for n := range team.RepoAllowanceNames {
		names = append(names, n)
	}
	sort.Strings(names)
	unknown := slices.ContainsFunc(given, func(a string) bool { return team.RepoAllowanceNames[a] == nil })
	if unknown || (clear && len(given) > 0) {
		fmt.Fprintf(os.Stderr, "allowances are %s; --clear takes none\n", strings.Join(names, ", "))
		return 1, true
	}
	s := protected.New(c.Home)
	err := s.CheckName(team.RepoAllowanceNS, repo)
	switch {
	case err != nil:
	case clear:
		err = s.Delete(team.RepoAllowanceNS, repo)
	case len(given) > 0:
		var once []string
		for _, a := range given {
			if !slices.Contains(once, a) {
				once = append(once, a)
			}
		}
		err = s.Put(team.RepoAllowanceNS, repo, strings.Join(once, "\n"))
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1, true
	}
	now, _ := s.Get(team.RepoAllowanceNS, repo)
	shown := strings.Join(strings.Fields(now), ", ")
	if shown == "" {
		shown = "nothing beyond the sandbox"
	}
	fmt.Printf("%s: %s\n", repo, shown)
	return 0, true
}
