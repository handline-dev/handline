package main

import (
	"fmt"
	"os"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/textsafe"
)

// progress keeps or shows the agent's note on its request in progress (cli/task.py cmd_progress).
func progress(c *hlclient.Client, args []string) (int, bool) {
	if len(args) > 1 || !plain(args) {
		return 0, false
	}
	c.NoteVerbChain([]string{"progress", ""})
	if c.CurrentAgent([]string{"progress"}) == "" {
		fmt.Fprintln(os.Stderr, hlclient.WhoRefusal("write"))
		return 1, true
	}
	if len(args) == 0 {
		got, err := c.Call("progress.get", nil)
		if err != nil {
			fmt.Fprintf(os.Stderr, "could not read the progress note: %v\n", err)
			return 1, true
		}
		note, _ := got["note"].(string)
		if note == "" {
			note = "no progress note"
		}
		fmt.Println(textsafe.Text(note))
		return 0, true
	}
	ok, said := c.Write("task.progress", map[string]any{"note": args[0]})
	if !ok {
		fmt.Fprintln(os.Stderr, said)
		return 1, true
	}
	fmt.Println(said)
	return 0, true
}
