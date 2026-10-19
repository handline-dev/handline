package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/handline/handline/go/internal/claude"
	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
)

// initCmd is `hl init` (setup.cmd_init): the home and its folders, the shared settings ocp keeps, its event hooks, a sandboxed agent let
// write under the home, reach the daemon's socket and run the read-only commands without asking, and the home kept in git.
func initCmd(c *hlclient.Client, args []string) (int, bool) {
	if len(args) != 0 {
		fmt.Fprintln(os.Stderr, "usage: hl init")
		return 2, true
	}
	h := c.Home
	for _, d := range []string{h, filepath.Join(h, "agents"), filepath.Join(h, "accounts")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			fmt.Fprintln(os.Stderr, "hl init:", err)
			return 1, true
		}
	}
	hooks := claude.Hooks{Home: h}
	if err := hooks.EnsureShared(); err != nil {
		fmt.Fprintln(os.Stderr, "hl init:", err)
		return 1, true
	}
	if ignore := filepath.Join(h, ".gitignore"); !exists(ignore) {
		_ = os.WriteFile(ignore, []byte("accounts/\ndb.sqlite\nlocks/\nlogs/\n*.tmp\n"), 0o644)
	}
	if cfg := paths.ConfigFile(h); !exists(cfg) {
		_ = os.WriteFile(cfg, []byte("version: 1\n"), 0o644)
	}
	if err := hooks.Install(paths.OcpExe()); err != nil {
		fmt.Fprintln(os.Stderr, "hl init:", err)
		return 1, true
	}
	fmt.Println("installed ocp event hooks")
	changed, blockers, err := hooks.AllowHomeWrites()
	if err != nil {
		fmt.Fprintln(os.Stderr, "hl init:", err)
		return 1, true
	}
	if changed {
		fmt.Printf("sandbox: agents may write under %s\n", h)
	}
	for _, e := range blockers {
		fmt.Printf("sandbox: denyWrite '%s' holds %s and still blocks agents from it; remove it from %s if that is not what you want\n",
			e, h, hooks.Target())
	}
	if before := readFile(hooks.Target()); hooks.AllowHomeSocket() == nil && readFile(hooks.Target()) != before {
		fmt.Printf("sandbox: agents may connect to %s\n", paths.SocketFile(h))
	}
	if added, err := hooks.AllowSafeCommands(); err == nil && added {
		fmt.Printf("permissions: safe commands run without asking (%s)\n", hooks.Target())
	}
	// The whole home is kept in git, so digest generations, rollback and change history come for free; accounts/ stays ignored.
	if git, err := exec.LookPath("git"); err == nil && !exists(filepath.Join(h, ".git")) {
		env := append(os.Environ(), "GIT_AUTHOR_NAME=ocp", "GIT_COMMITTER_NAME=ocp", "GIT_AUTHOR_EMAIL=ocp@localhost", "GIT_COMMITTER_EMAIL=ocp@localhost")
		for _, a := range [][]string{{"init", "-q"}, {"add", "-A"}, {"commit", "-q", "-m", "ocp init"}} {
			cmd := exec.Command(git, a...)
			cmd.Dir, cmd.Env = h, env
			_ = cmd.Run()
		}
		fmt.Printf("initialized git repo in %s\n", h)
	}
	fmt.Printf("initialized %s\n", h)
	fmt.Println("next: add an account in the app's Accounts page - it registers the account and logs it in")
	return 0, true
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func readFile(p string) string {
	b, _ := os.ReadFile(p)
	return string(b)
}
