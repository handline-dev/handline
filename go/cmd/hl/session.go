package main

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
)

// closeCmd ends an idle agent's session, keeping its terminal (cli/task.py cmd_close), through session.stop mode close.
func closeCmd(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, []string{"--fresh"}, nil)
	if !ok || len(p.pos) != 1 || !ascii(p.pos[0]) {
		return 0, false
	}
	c.NoteVerbChain([]string{"close", ""})
	raw := p.pos[0]
	if notAgent(raw) {
		fmt.Printf("'%s' is not an agent. A task has no close: `hl task done %s` ends it, "+
			"and `hl task reopen %s` undoes a wrong result.\n", raw, raw, raw)
		return 1, true
	}
	params := map[string]any{"agent": raw, "mode": "close"}
	if p.flags["--fresh"] {
		params["fresh"] = true
	}
	return said(c.Write("session.stop", params)), true
}

// restart ends an agent's session and starts it again in the same terminal (cli/task.py cmd_restart).
func restart(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, []string{"--fresh", "--force"}, nil)
	if !ok || len(p.pos) != 1 || !ascii(p.pos[0]) {
		return 0, false
	}
	c.NoteVerbChain([]string{"restart", ""})
	raw := p.pos[0]
	if notAgent(raw) {
		fmt.Printf("'%s' is not an agent.\n", raw)
		return 1, true
	}
	params := map[string]any{"agent": raw}
	for _, f := range []string{"fresh", "force"} {
		if p.flags["--"+f] {
			params[f] = true
		}
	}
	return said(c.Write("session.restart", params)), true
}

// notAgent is a number or a task reference, which close and restart refuse before asking the daemon.
func notAgent(raw string) bool {
	_, task := taskRef(raw)
	return digits(raw) || (task && strings.TrimSpace(raw) == raw)
}

// ascii keeps what Python's str.isdigit and \d would read differently from Go's (a non-ASCII digit) on Python's side.
func ascii(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}
