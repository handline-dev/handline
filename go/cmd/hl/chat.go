package main

import (
	"fmt"
	"os"
	"time"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
)

// chat is `hl chat search`: the caller's own messages that hold the text, newest first.
func chat(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 || args[0] != "search" {
		return 0, false
	}
	p, ok := parse(args[1:], []string{"--with", "--since", "--limit"}, nil, nil)
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	params := map[string]any{"query": p.pos[0]}
	if v, has := p.opt("--with"); has {
		params["with"] = v
	}
	if v, has := p.opt("--since"); has {
		secs, err := timefmt.ParseSpan(v)
		if err != nil {
			return wrongArgs("--since is a span like 2h or 3d")
		}
		params["since"] = time.Now().Add(-time.Duration(secs) * time.Second).UTC().Format(time.RFC3339)
	}
	if v, has := p.opt("--limit"); has {
		n, ok := integer(v)
		if !ok || n <= 0 {
			return wrongArgs("--limit is a positive number")
		}
		params["limit"] = n
	}
	res, err := c.Call("chat.search", params)
	if err != nil {
		fmt.Fprintf(os.Stderr, "could not search: %v\n", err)
		return 1, true
	}
	hits := list(res, "messages")
	if len(hits) == 0 {
		fmt.Println("nothing found")
		return 0, true
	}
	for _, m := range hits {
		when := str(m, "at")
		if t, err := time.Parse(time.RFC3339, when); err == nil {
			when = timefmt.Stamp(t.Unix(), "01-02 15:04")
		}
		fmt.Printf("%s  %s  %s → %s: %s\n", str(m, "ref"), when, str(m, "from"), str(m, "to"), textsafe.Oneline(clipRunes(str(m, "body"), 160)))
	}
	return 0, true
}

func clipRunes(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n]) + "…"
	}
	return s
}
