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
	"github.com/handline/handline/go/internal/yamlcompat"
)

// hr raises a roster change for the founder to decide, lists proposals, or takes one back (cli/hr.cmd_hr). The founder approves and rejects from
// the app's card or with hl hr approve/reject; either is `hr.decide`.
func hr(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 {
		return 0, false
	}
	switch sub, rest := args[0], args[1:]; sub {
	case "add", "retire", "rename", "link", "unlink", "team", "context":
		return hrPropose(c, sub, rest)
	case "diff":
		return hrDiff(c, rest)
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
	case "approve", "reject":
		return hrDecide(c, sub, rest)
	}
	return 0, false
}

// hrDecide is the founder's approve or reject of a proposal, as the app's card makes it (hr.decide): an approve names the subject hr.list
// shows, so what is approved is what was listed. An agent's session is refused here; the daemon refuses any caller but the founder.
func hrDecide(c *hlclient.Client, verb string, args []string) (int, bool) {
	p, ok := parse(args, nil, nil, nil)
	if !ok || len(p.pos) < 1 || verb == "approve" && len(p.pos) != 1 {
		return 0, false
	}
	n, err := strconv.ParseInt(p.pos[0], 16, 64)
	if err != nil {
		fmt.Printf("no proposal %s\n", p.pos[0])
		return 1, true
	}
	if hlclient.Agent() != "" {
		fmt.Printf("refused: proposal %s is the founder's to decide, in the app's HR tab or with `hl hr %s %s` at their own terminal\n", p.pos[0], verb, p.pos[0])
		return 1, true
	}
	params := map[string]any{"proposal": n, "decision": verb}
	if verb == "reject" && len(p.pos) > 1 {
		params["note"] = strings.Join(p.pos[1:], " ")
	}
	if verb == "approve" {
		got, err := c.Call("hr.list", nil)
		if err != nil {
			fmt.Fprintf(os.Stderr, "the proposals cannot be read: %v\n", err)
			return 1, true
		}
		for _, r := range rows(got["proposals"]) {
			if anum(r["id"]) == n {
				params["subject"] = text(r["subject"])
			}
		}
		if params["subject"] == nil {
			fmt.Printf("no proposal %s waits on you\n", p.pos[0])
			return 1, true
		}
	}
	return said(c.Write("hr.decide", params)), true
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
	if !ok || len(p.pos) != want {
		return 0, false
	}
	why, hasWhy := p.opt("--why")
	if !hasWhy {
		return 0, false
	}
	if _, ok := p.opt("--to"); kind == "rename" && !ok {
		return 0, false
	}
	if len(p.each["--member"]) == 0 && kind == "team" {
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

// expandHome is a path with a leading ~/ taken to the home folder.
func expandHome(path string) string {
	if strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, path[2:])
		}
	}
	return path
}

// hrDiff raises a whole team diff from a file's `changes:` as one proposal (cli/hr._read_changes, hr.propose of kind bundle).
func hrDiff(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--file", "--why"}, nil, nil)
	file, hasFile := p.opt("--file")
	why, hasWhy := p.opt("--why")
	if !ok || len(p.pos) != 0 || !hasFile || !hasWhy {
		return 0, false
	}
	if c.AskingAgent() == "" {
		fmt.Println(hlclient.WhoRefusal("propos"))
		return 1, true
	}
	changes, err := readChanges(file)
	if err != "" {
		fmt.Fprintln(os.Stderr, err)
		return 1, true
	}
	c.NoteVerbChain([]string{"hr", "diff"})
	args2 := map[string]any{"name": fmt.Sprintf("%d changes", len(changes)), "changes": changes}
	return said(c.Write("hr.propose", map[string]any{"kind": "bundle", "why": why, "args": args2})), true
}

// readChanges is the `changes:` of a team-diff file, a file named by role_file or base_file read in as the new text of a context change.
func readChanges(path string) ([]any, string) {
	path = expandHome(path)
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Sprintf("%s: %v", path, err)
	}
	doc, err := yamlcompat.Parse(string(b))
	if err != nil {
		return nil, fmt.Sprintf("%s: %v", path, err)
	}
	top, _ := doc.(map[string]any)
	list, isList := top["changes"].([]any)
	if !isList {
		return nil, path + ": needs a `changes:` list"
	}
	out := make([]any, 0, len(list))
	for _, raw := range list {
		ch, isMap := raw.(map[string]any)
		if !isMap {
			return nil, path + ": each change is a map"
		}
		ch2 := map[string]any{}
		for k, v := range ch {
			ch2[k] = v
		}
		texts := map[string]any{}
		if sets, isSets := ch["sets"].(map[string]any); isSets {
			for k, v := range sets {
				texts[k] = v
			}
		}
		for _, f := range [][2]string{{"role_file", "ROLE.md"}, {"base_file", "base.md"}} {
			ref, has := ch2[f[0]]
			if !has {
				continue
			}
			delete(ch2, f[0])
			name := expandHome(fmt.Sprint(ref))
			if !filepath.IsAbs(name) {
				name = filepath.Join(filepath.Dir(path), name)
			}
			body, err := os.ReadFile(name)
			if err != nil {
				return nil, fmt.Sprintf("%s: %v", path, err)
			}
			texts[f[1]] = string(body)
		}
		if len(texts) > 0 {
			ch2["sets"] = texts
		}
		out = append(out, ch2)
	}
	return out, ""
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
