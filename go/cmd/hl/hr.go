package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// hr raises a roster change for the founder to decide, lists proposals, or takes one back (cli/hr.cmd_hr). The founder approves and rejects from
// the app: an approve is `hr.decide`, which a person makes from a card.
func hr(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 {
		return 0, false
	}
	switch sub, rest := args[0], args[1:]; sub {
	case "add", "retire", "link", "unlink", "context":
		return hrPropose(c, sub, rest)
	case "withdraw":
		p, ok := parse(rest, nil, nil, nil)
		if !ok || len(p.pos) != 1 {
			return 0, false
		}
		if c.AskingAgent() == "" {
			fmt.Println(hlclient.WhoRefusal("withdraw"))
			return 1, true
		}
		n, err := strconv.ParseInt(p.pos[0], 16, 64)
		if err != nil {
			fmt.Printf("no proposal %s\n", p.pos[0])
			return 1, true
		}
		return said(c.Write("hr.withdraw", map[string]any{"proposal": n})), true
	case "list":
		return hrList(c, rest)
	}
	return 0, false
}

func hrPropose(c *hlclient.Client, kind string, args []string) (int, bool) {
	valued := []string{"--why"}
	switch kind {
	case "add":
		valued = append(valued, "--team", "--repo", "--model", "--effort", "--vendor")
	case "link":
		valued = append(valued, "--response", "--relation")
	case "context":
		valued = append(valued, "--role-file", "--base-file")
	}
	p, ok := parse(args, valued, nil, nil)
	want := 1
	if kind == "link" || kind == "unlink" {
		want = 2
	}
	if !ok || len(p.pos) != want {
		return 0, false
	}
	why, hasWhy := p.opt("--why")
	if !hasWhy {
		return 0, false
	}
	if effort, ok := p.opt("--effort"); ok && !slices.Contains(team.ValidEffort, effort) {
		return 0, false
	}
	me := c.AskingAgent()
	if me == "" {
		fmt.Println(hlclient.WhoRefusal("propos"))
		return 1, true
	}
	c.NoteVerbChain([]string{"hr", kind})
	hargs := map[string]any{"name": p.pos[0]}
	params := map[string]any{"kind": kind, "why": why, "args": hargs}
	if want == 2 {
		hargs["to"] = p.pos[1]
	}
	for _, o := range []string{"team", "repo", "model", "effort", "vendor", "response", "relation"} {
		if v, ok := p.opt("--" + o); ok {
			hargs[o] = v
		}
	}
	if kind == "context" {
		sets := map[string]any{}
		for _, f := range [][2]string{{"ROLE.md", "--role-file"}, {"base.md", "--base-file"}} {
			if path, ok := p.opt(f[1]); ok && path != "" {
				if strings.HasPrefix(path, "~/") {
					if home, err := os.UserHomeDir(); err == nil {
						path = filepath.Join(home, path[2:])
					}
				}
				b, err := os.ReadFile(path)
				if err != nil {
					fmt.Fprintf(os.Stderr, "%s: %v\n", path, err)
					return 1, true
				}
				sets[f[0]] = string(b)
			}
		}
		hargs["sets"] = sets
	}
	return said(c.Write("hr.propose", params)), true
}

func hrList(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, []string{"--json", "--all"}, nil)
	if !ok || len(p.pos) != 0 {
		return 0, false
	}
	got, err := c.Call("hr.list", nil)
	if err != nil {
		fmt.Fprintf(os.Stderr, "the proposals cannot be read: %v\n", err)
		return 1, true
	}
	props := rows(got["proposals"])
	if p.flags["--json"] {
		if !p.flags["--all"] {
			props = slices.DeleteFunc(props, func(r map[string]any) bool { s := text(r["state"]); return s != "open" && s != "apply_failed" })
		}
		out, _ := json.MarshalIndent(props, "", "  ")
		fmt.Println(string(out))
		return 0, true
	}
	if len(props) == 0 {
		fmt.Println("no proposals")
		return 0, true
	}
	var names []string
	for _, r := range props {
		names = append(names, textsafe.Oneline(text(r["agent"])))
	}
	w := nameCol(names)
	for _, r := range props {
		by := text(r["requested_by"])
		if by == "" {
			by = "?"
		}
		fmt.Printf("%08x  %s%s%sby %s\n", anum(r["id"]), pad(text(r["kind"]), 7), pad(textsafe.Oneline(text(r["agent"])), w), pad(text(r["state"]), 13), textsafe.Oneline(by))
	}
	return 0, true
}
