package main

import (
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
)

// waitMax is how long the daemon holds one waiting call at most (cli/ask.WAIT_MAX_S); a longer wait asks again. waitDefault is a --wait's own limit.
const (
	waitMax     = 600
	waitDefault = 600
)

// nameCol is how wide a column of agent names has to be (cli/health._name_col).
func nameCol(names []string) int {
	w := 12
	for _, n := range names {
		w = max(w, len([]rune(n))+2)
	}
	return w
}

func pad(s string, w int) string {
	if n := len([]rune(s)); n < w {
		return s + strings.Repeat(" ", w-n)
	}
	return s
}

// withdraw takes back a request of the caller's own before it is delivered (cli/ask.cmd_withdraw).
func withdraw(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--reason"}, nil, nil)
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	id, ok := integer(p.pos[0])
	if !ok {
		return 0, false
	}
	reason, hasReason := p.opt("--reason")
	if hasReason && reason == "" {
		fmt.Println("--reason was given empty; type one or leave the flag off")
		return 1, true
	}
	c.NoteVerbChain([]string{"withdraw", ""})
	if c.AskingAgent() == "" {
		fmt.Println(hlclient.WhoRefusal("withdraw"))
		return 1, true
	}
	params := map[string]any{"id": id}
	if hasReason {
		params["reason"] = reason
	}
	return said(c.Write("request.withdraw", params)), true
}

// listingRefusal is why caller may not list target's messages, or "" (actions.listing_refusal).
func listingRefusal(c *hlclient.Client, caller, target string) string {
	if caller == "" || caller == hlclient.Human || caller == target {
		return ""
	}
	if t, err := team.LoadTeam(c.Home); err == nil && t.SupervisorOf(target) == caller {
		return ""
	}
	return fmt.Sprintf("%s may not list %s's messages: only %s, its supervisor and the human may. `hl read <id>` shows one request by number.", caller, target, target)
}

func anum(v any) int64 {
	switch n := v.(type) {
	case float64:
		return int64(n)
	case int64:
		return n
	case int:
		return int64(n)
	}
	return 0
}

func text(v any) string { s, _ := v.(string); return s }

func rows(v any) []map[string]any {
	var out []map[string]any
	list, _ := v.([]any)
	for _, r := range list {
		if m, ok := r.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// inbox shows an agent's queue head, or with --all its history; the founder's is the mailbox (cli/ask.cmd_inbox).
func inbox(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--timeout"}, []string{"--all", "--wait"}, nil)
	if !ok || len(p.pos) > 1 {
		return 0, false
	}
	timeout := waitDefault
	if v, has := p.opt("--timeout"); has {
		if timeout, ok = integer(v); !ok {
			return 0, false
		}
	}
	agent := ""
	if len(p.pos) == 1 {
		agent = p.pos[0]
	} else {
		agent = c.AskingAgent()
	}
	if agent == "" {
		if hlclient.Agent() != "" {
			fmt.Println(hlclient.WhoRefusal("read the inbox"))
		} else {
			fmt.Println("cannot tell whose inbox this is: OCP_AGENT is unset and this is not an interactive shell. Name the agent.")
		}
		return 1, true
	}
	caller := c.AskingAgent()
	if caller == "" {
		fmt.Println(refusedListing())
		return 1, true
	}
	if why := listingRefusal(c, caller, agent); why != "" {
		fmt.Println(why)
		return 1, true
	}
	all, wait := p.flags["--all"], p.flags["--wait"]
	params := map[string]any{"whose": agent, "all": all}
	got, err := c.Call("inbox.queue", params)
	var since int64
	if err == nil && wait {
		since = anum(got["newest"])
		for left := timeout; left > 0; {
			hold := min(left, waitMax)
			q := map[string]any{"whose": agent, "all": all, "since": since, "wait_s": hold}
			if got, err = c.Call("inbox.queue", q); err != nil || anum(got["newest"]) > since {
				break
			}
			left -= hold
		}
	}
	if err != nil {
		var rpcErr *hlclient.RPCError
		if errors.As(err, &rpcErr) {
			fmt.Println(err)
		} else {
			fmt.Fprintf(os.Stderr, "could not read the inbox: %v\n", err)
		}
		return 1, true
	}
	if wait && anum(got["newest"]) <= since {
		fmt.Fprintf(os.Stderr, "nothing after %ds: nothing new for %s\n", timeout, agent)
		return 0, true
	}
	if agent == hlclient.Human {
		return humanInbox(got, all), true
	}
	if all {
		hist := rows(got["history"])
		if len(hist) == 0 {
			fmt.Printf("%s: inbox empty\n", agent)
			return 0, true
		}
		fmt.Printf("%d total. `hl read <id>` shows one whole.\n", len(hist))
		for _, r := range hist {
			fmt.Printf("#%-5d from %s %s depth=%d %s\n", anum(r["id"]), pad(text(r["requester"]), 12), pad(text(r["state"]), 9),
				anum(r["chain_depth"]), textsafe.Oneline(text(r["line"])))
		}
		return 0, true
	}
	head, _ := got["head"].(map[string]any)
	if head == nil {
		fmt.Printf("%s: inbox empty\n", agent)
		return 0, true
	}
	fmt.Printf("#%d from %s  depth=%d\n\n", anum(head["id"]), text(head["requester"]), anum(head["chain_depth"]))
	body := text(head["body"])
	if body == "" {
		body = "(empty)"
	}
	fmt.Println(textsafe.Text(body))
	if more := text(head["more_waiting_line"]); more != "" {
		fmt.Printf("\n%s\n", textsafe.Oneline(more))
	}
	fmt.Printf("\n%s. `hl inbox --all` shows the whole queue and its history.\n", textsafe.Oneline(text(head["how_to_close"])))
	// Only a look at one's own queue counts as being told.
	if agent == caller {
		if ok, why := c.Write("inbox.mark_read", map[string]any{"ids": []any{anum(head["id"])}}); !ok {
			fmt.Fprintf(os.Stderr, "#%d is shown but not marked read: %s\n", anum(head["id"]), why)
		}
	}
	return 0, true
}

func refusedListing() string {
	if hlclient.Agent() != "" {
		return hlclient.WhoRefusal("list") + ", so nothing is listed"
	}
	return "cannot tell who is asking: OCP_AGENT is unset and this is not an interactive shell, so nothing is listed."
}

// humanInbox is the founder's mailbox (cli/ask._human_inbox).
func humanInbox(got map[string]any, showAll bool) int {
	lines := rows(got["lines"])
	if len(lines) == 0 && !showAll {
		fmt.Println("nothing waiting on you")
		return 0
	}
	fmt.Printf("%d waiting. `hl read <id>` shows one whole.\n", anum(got["waiting"]))
	for _, m := range lines {
		mark := "."
		if m["decision"] == true {
			mark = "!"
		} else if m["answer"] != nil && m["answer"] != false {
			mark = "<"
		}
		behind := ""
		if u := anum(m["unread"]); u > 1 {
			behind = fmt.Sprintf(" (+%d)", u-1)
		}
		fmt.Printf("%s #%-5d %s %s %s%s\n", mark, anum(m["id"]), pad(text(m["from"]), 12), pad(text(m["kind"]), 9), textsafe.Oneline(text(m["line"])), behind)
	}
	fmt.Println("`hl say <who> --parent <id> \"<text>\"` answers one - a reply settles what it answers, and on a `<` row it goes back in that thread.")
	if showAll {
		dealt := rows(got["dealt"])
		fmt.Printf("\n%d most recent, already dealt with:\n", len(dealt))
		for _, r := range dealt {
			arrow := "←"
			if r["outgoing"] == true {
				arrow = "→"
			}
			fmt.Printf("  #%-5d %s %s %s %s\n", anum(r["id"]), arrow, pad(text(r["agent"]), 12), pad(text(r["state"]), 9), textsafe.Oneline(text(r["line"])))
		}
	}
	return 0
}

var neverAnswered = map[string]string{"denied": "refused", "withdrawn": "withdrawn", "replaced": "replaced"}

func settled(state string) bool {
	_, never := neverAnswered[state]
	return never || state == "landed"
}

func size(n int64) string {
	switch {
	case n < 1024:
		return fmt.Sprintf("%d B", n)
	case n < 1<<20:
		return fmt.Sprintf("%d KB", (n+512)/1024)
	}
	return fmt.Sprintf("%.1f MB", float64(n)/(1<<20))
}

// read prints one request whole, with its answer (cli/ask.cmd_read).
func read(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--timeout"}, []string{"--wait"}, nil)
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	id, ok := integer(p.pos[0])
	if !ok {
		return 0, false
	}
	timeout := waitDefault
	if v, has := p.opt("--timeout"); has {
		if timeout, ok = integer(v); !ok {
			return 0, false
		}
	}
	wait := p.flags["--wait"]
	left := 0
	if wait {
		left = timeout
	}
	var row map[string]any
	for {
		hold := min(left, waitMax)
		var err error
		row, err = c.Call("request.read", map[string]any{"id": id, "wait_s": hold})
		if err != nil {
			var rpcErr *hlclient.RPCError
			if errors.As(err, &rpcErr) && rpcErr.Code == "not_found" {
				fmt.Printf("no request #%d\n", id)
			} else {
				fmt.Fprintf(os.Stderr, "could not read #%d: %v\n", id, err)
			}
			return 1, true
		}
		left -= hold
		if settled(text(row["state"])) || left <= 0 {
			break
		}
	}
	if wait && !settled(text(row["state"])) {
		fmt.Fprintf(os.Stderr, "nothing after %ds: #%d has no answer yet\n", timeout, id)
		return 1, true
	}
	when := text(row["created"])
	if ts, err := time.Parse(time.RFC3339, when); err == nil {
		when = timefmt.Stamp(ts.Unix(), "2006-01-02 15:04")
	}
	fmt.Printf("#%d  %s → %s  %s  %s  %s\n", anum(row["id"]), text(row["requester"]), text(row["target"]), text(row["kind"]), text(row["state"]), when)
	if parent, ok := row["parent"].(map[string]any); ok {
		line := ""
		if l := text(parent["line"]); l != "" {
			line = ": " + textsafe.Oneline(l)
		}
		fmt.Printf("in reply to #%d%s\n", anum(parent["id"]), line)
	}
	fmt.Printf("relation %v   response %v   depth %d\n\n", orNone(row["relation"]), orNone(row["response"]), anum(row["chain_depth"]))
	body := text(row["body"])
	if body == "" {
		body = "(empty)"
	}
	fmt.Println(textsafe.Text(body))
	n := 0
	for _, part := range rows(row["parts"]) {
		img, _ := part["image"].(map[string]any)
		if path := text(part["path"]); path != "" && img != nil {
			n++
			fmt.Printf("[image %d: %s (%s, %s)]\n", n, path, text(img["mime"]), size(anum(img["bytes"])))
		}
	}
	if d, ok := row["dismissal"].(map[string]any); ok && d != nil {
		by, reason := text(d["by"]), text(d["reason"])
		head := "--- dismissed"
		if by != "" {
			head += " by " + textsafe.Oneline(by)
		}
		fmt.Printf("\n%s ---\n", head)
		if reason != "" {
			fmt.Println(textsafe.Text(reason))
		}
	} else if ans := text(row["answer"]); ans != "" {
		if h, never := neverAnswered[text(row["state"])]; never {
			fmt.Printf("\n--- %s ---\n", h)
		} else {
			fmt.Printf("\n--- answered by %s ---\n", text(row["target"]))
		}
		fmt.Println(textsafe.Text(ans))
	}
	return 0, true
}

func orNone(v any) any {
	if v == nil {
		return "None"
	}
	return v
}

// teamCmd shows who the caller may ask and what they are doing (cli/team.cmd_team).
func teamCmd(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--agent"}, nil, nil)
	if !ok || len(p.pos) != 0 {
		return 0, false
	}
	me, _ := p.opt("--agent")
	if me == "" {
		me = c.AskingAgent()
	}
	if me == "" {
		fmt.Println(hlclient.WhoRefusal("ask"))
		return 1, true
	}
	t, err := team.LoadTeam(c.Home)
	if err != nil {
		fmt.Printf("team.yaml is invalid: %v\n", err)
		return 1, true
	}
	lines := map[string]map[string]any{}
	if got, err := c.Call("agents.list", map[string]any{"status": true}); err != nil {
		fmt.Fprintf(os.Stderr, "could not read what they are doing: %v\n", err)
	} else {
		for _, a := range rows(got["agents"]) {
			lines[text(a["name"])] = a
		}
	}
	type mine struct{ name, rel, doing string }
	type theirs struct{ name, rel string }
	var outs []mine
	var ins []theirs
	var names []string
	for _, other := range paths.ListAgents(c.Home) {
		if other == me {
			continue
		}
		if d := t.Route(me, other, 0, false); d.Allowed {
			outs = append(outs, mine{other, inYourWords(d.Relation, true), doing(other, lines[other])})
			names = append(names, other)
		}
		if d := t.Route(other, me, 0, false); d.Allowed {
			ins = append(ins, theirs{other, inYourWords(d.Relation, false)})
			names = append(names, other)
		}
	}
	w := nameCol(names)
	fmt.Printf("you are %s\n", me)
	if len(outs) > 0 {
		fmt.Println("\nyou may ask:")
		for _, o := range outs {
			fmt.Printf("  %s%s%s\n", pad(o.name, w), pad(o.rel, 12), o.doing)
		}
	} else {
		fmt.Println("\nthere is nobody you may ask.")
	}
	if len(ins) > 0 {
		var parts []string
		for _, i := range ins {
			parts = append(parts, fmt.Sprintf("%s (%s)", i.name, i.rel))
		}
		fmt.Printf("\nmay ask you: %s\n", strings.Join(parts, ", "))
	}
	fmt.Println("\n`hl task new --assign <name> -t <time> \"<title>\" \"<body>\"` gives one work, and `hl say <name> \"...\"` sends a message. " +
		"A task is the only way to give an agent work:\na subagent you spawn yourself dies with this session and nobody can see it.")
	return 0, true
}

func inYourWords(relation *string, sending bool) string {
	switch {
	case relation != nil && *relation == "supervisor":
		if sending {
			return "your report"
		}
		return "your manager"
	case relation != nil && *relation == "subordinate":
		if sending {
			return "your manager"
		}
		return "your report"
	}
	return "peer"
}

// doing is one line on what an agent is doing, from its agents.list status line ("?" when the daemon gave none).
func doing(agent string, line map[string]any) string {
	if line == nil {
		return "?"
	}
	if line["has_repo"] != true && line["has_terminal"] != true {
		return fmt.Sprintf("never started - no repo (`hl agent register %s --repo <path>`)", agent)
	}
	if line["started"] != true {
		return "never started"
	}
	state := text(line["screen_word"])
	if d := text(line["screen_detail"]); d != "" {
		state += " " + textsafe.Oneline(d)
	}
	if n := anum(line["unreached"]); n != 0 {
		state += fmt.Sprintf(" · %d waiting", n)
	}
	if waits := rows(line["task_waits"]); len(waits) > 0 {
		state += fmt.Sprintf(" · T%d waits: %s", anum(waits[0]["task"]), textsafe.Oneline(text(waits[0]["words"])))
		if len(waits) > 1 {
			state += fmt.Sprintf(" (+%d more)", len(waits)-1)
		}
	}
	if w := text(line["wake_wait_reason"]); w != "" {
		state += " · " + textsafe.Oneline(w)
	}
	if line["has_terminal"] != true {
		state += " · no terminal yet"
	}
	return state
}
