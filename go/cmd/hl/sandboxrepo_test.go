package main

import (
	"testing"

	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/team"
)

func TestSandboxRepoShowsSetsAndClearsARepoAllowances(t *testing.T) {
	r := hookRig(t, false)
	for _, c := range []struct {
		args      []string
		out, errs string
		code      int
	}{
		{[]string{"sandbox-repo", "web"}, "web: nothing beyond the sandbox\n", "", 0},
		{[]string{"sandbox-repo", "web", "pnpm-install", "weaker-network", "pnpm-install"}, "web: pnpm-install, weaker-network\n", "", 0},
		{[]string{"sandbox-repo", "web"}, "web: pnpm-install, weaker-network\n", "", 0},
		{[]string{"sandbox-repo", "web", "root"}, "", "allowances are pnpm-install, weaker-network; --clear takes none\n", 1},
		{[]string{"sandbox-repo", "web", "--clear", "pnpm-install"}, "", "allowances are pnpm-install, weaker-network; --clear takes none\n", 1},
		{[]string{"sandbox-repo", "../x"}, "", "not a plain name: \"../x\"\n", 1},
		{[]string{"sandbox-repo"}, "", sandboxRepoUsage + "\n", 2},
		{[]string{"sandbox-repo", "web", "--clear"}, "web: nothing beyond the sandbox\n", "", 0},
	} {
		out, errs, code := r.run(nil, c.args...)
		if out != c.out || errs != c.errs || code != c.code {
			t.Errorf("%v: out %q err %q exit %d", c.args, out, errs, code)
		}
	}
	if _, ok := protected.New(r.home).Get(team.RepoAllowanceNS, "web"); ok {
		t.Fatal("--clear left the entry")
	}
}

func TestSandboxRepoIsTheFoundersToSet(t *testing.T) {
	r := hookRig(t, false)
	_, errs, code := r.run(asAgent, "sandbox-repo", "web", "pnpm-install")
	if code != 1 || errs != protected.ErrNotTheFounder.Error()+"\n" {
		t.Fatalf("err %q exit %d", errs, code)
	}
}
