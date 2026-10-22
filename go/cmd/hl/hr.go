package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// hr raises a roster change for the founder to decide, lists proposals, or takes one back (cli/hr.cmd_hr). The founder decides in the app.
func hr(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 {
		return 0, false
	}
	switch sub, rest := args[0], args[1:]; sub {
	case "add", "retire", "rename", "link", "unlink", "team", "context":
		return hrPropose(c, sub, rest)
	case "bundle":
		return hrBundle(c, rest)
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
	case "context":
		valued = append(valued, "--role-file", "--base-file")
	case "rename":
		valued = append(valued, "--to", "--team")
	case "team":
		valued = append(valued, "--member", "--repo", "--model", "--effort", "--vendor")
	}
	p, ok := parse(args, valued, nil, nil)
	want := 1
	if kind == "link" || kind == "unlink" {
		want = 2
	}
	if !ok {
		return 0, false
	}
	if len(p.pos) != want {
		return wrongArgs("it takes %s, and got %d word(s)", map[int]string{1: "one agent", 2: "two agents"}[want], len(p.pos))
	}
	why, problem := hrWhy(p)
	if problem != "" {
		return wrongArgs("%s", problem)
	}
	if _, ok := p.opt("--to"); kind == "rename" && !ok {
		return wrongArgs("--to is missing: the new name")
	}
	if len(p.each["--member"]) == 0 && kind == "team" {
		return wrongArgs("--member is missing: at least one")
	}
	if effort, ok := p.opt("--effort"); ok && !slices.Contains(team.ValidEffort, effort) {
		return wrongArgs("--effort is one of %s", strings.Join(team.ValidEffort, ", "))
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
	for _, o := range []string{"team", "repo", "model", "effort", "vendor", "to"} {
		if v, ok := p.opt("--" + o); ok {
			hargs[o] = v
		}
	}
	if kind == "team" {
		var members []string
		repos := map[string]any{}
		for _, m := range p.each["--member"] {
			n, r, hasRepo := strings.Cut(m, "=")
			members = append(members, n)
			if hasRepo && r != "" {
				if !strings.HasPrefix(n, p.pos[0]+".") {
					n = p.pos[0] + "." + n
				}
				repos[n] = r
			}
		}
		if r, ok := p.opt("--repo"); ok {
			repos[p.pos[0]] = r
		}
		hargs["members"], hargs["repos"] = members, repos
	}
	if kind == "context" {
		sets := map[string]any{}
		for _, f := range [][2]string{{"ROLE.md", "--role-file"}, {"base.md", "--base-file"}} {
			if path, ok := p.opt(f[1]); ok && path != "" {
				path = expandHome(path)
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

// hrBundle raises several changes as one proposal, decided once: --file is a JSON list of changes, each {"kind", "name", ...} as hr.propose takes them.
func hrBundle(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--why", "--file"}, nil, nil)
	if !ok || len(p.pos) != 0 {
		return 0, false
	}
	why, problem := hrWhy(p)
	if problem != "" {
		return wrongArgs("%s", problem)
	}
	path, ok := p.opt("--file")
	if !ok {
		return wrongArgs("--file is missing: a JSON list of changes")
	}
	b, err := os.ReadFile(expandHome(path))
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", path, err)
		return 1, true
	}
	var changes []any
	if err := json.Unmarshal(b, &changes); err != nil {
		fmt.Fprintf(os.Stderr, "%s is not a JSON list of changes: %v\n", path, err)
		return 1, true
	}
	if c.AskingAgent() == "" {
		fmt.Println(hlclient.WhoRefusal("propos"))
		return 1, true
	}
	c.NoteVerbChain([]string{"hr", "bundle"})
	return said(c.Write("hr.propose", map[string]any{"kind": "bundle", "why": why,
		"args": map[string]any{"name": fmt.Sprintf("%d changes", len(changes)), "changes": changes}})), true
}

// hrWhy is --why, or what is wrong with it: missing, or naming a message the founder cannot read.
func hrWhy(p parsed) (why, problem string) {
	why, ok := p.opt("--why")
	if !ok {
		return "", "--why is missing: say why, for the founder to decide"
	}
	if ref := messageRef.FindString(why); ref != "" {
		return "", "--why names " + ref + ": say the reason in words, a message number means nothing to the founder"
	}
	return why, ""
}

// messageRef is a message number in a reason (the app's MESSAGE_REF).
var messageRef = regexp.MustCompile(`\b(?:msg_[0-9a-f]{7}|ocp-msg [0-9a-f]{8,})\b`)

// expandHome is a path with a leading ~/ taken to the home folder.
func expandHome(path string) string {
	if strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, path[2:])
		}
	}
	return path
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
	props := list(got, "proposals")
	if p.flags["--json"] {
		if !p.flags["--all"] {
			props = slices.DeleteFunc(props, func(r map[string]any) bool { s := str(r, "state"); return s != "open" && s != "apply_failed" })
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
		names = append(names, textsafe.Oneline(str(r, "agent")))
	}
	w := nameCol(names)
	for _, r := range props {
		by := str(r, "requested_by")
		if by == "" {
			by = "?"
		}
		fmt.Printf("%08x  %s%s%sby %s\n", num(r, "id"), pad(str(r, "kind"), 7), pad(textsafe.Oneline(str(r, "agent")), w), pad(str(r, "state"), 13), textsafe.Oneline(by))
	}
	return 0, true
}
