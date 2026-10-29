package main

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/hlclient"
)

// worktree is `hl worktree add <path>`: the agent records a worktree it works in beside the one its session started in, so the
// founder's list never offers it as an orphan to remove (worktree.declare).
func worktree(c *hlclient.Client, args []string) (int, bool) {
	if len(args) != 2 || args[0] != "add" || !plain(args) {
		return 0, false
	}
	c.NoteVerbChain([]string{"worktree", "add"})
	if c.CurrentAgent() == "" {
		why := "cannot tell who is writing: OCP_AGENT is unset"
		if hlclient.Agent() != "" {
			why = hlclient.WhoRefusal("write")
		}
		fmt.Fprintln(os.Stderr, why)
		return 1, true
	}
	path, err := filepath.Abs(args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1, true
	}
	ok, said := c.Write("worktree.declare", map[string]any{"path": path})
	if !ok {
		fmt.Fprintln(os.Stderr, said)
		return 1, true
	}
	fmt.Println(said)
	return 0, true
}
