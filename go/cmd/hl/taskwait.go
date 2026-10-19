package main

import (
	"fmt"
	"os/exec"
	"regexp"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
)

var githubOrigin = regexp.MustCompile(`github\.com[:/]([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$`)

// originRepo is owner/name of the GitHub origin of the checkout hl runs in, "" when there is none.
func originRepo() string {
	out, err := exec.Command("git", "remote", "get-url", "origin").Output()
	if err != nil {
		return ""
	}
	if m := githubOrigin.FindStringSubmatch(strings.TrimSpace(string(out))); m != nil {
		return m[1]
	}
	return ""
}

// taskWait is hl task wait T<n> --pr <n> [--ci | --close] [--until <span>] [--repo owner/name]: a gate on the PR (task.new, owner gate),
// T<n> blocked by it (task.edit) and, when doing, released (task.release), so its holder takes other work and T<n> comes back to its queue
// when the PR's CI finishes (the default) or the PR closes.
func taskWait(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--pr", "--until", "--repo"}, []string{"--ci", "--close"}, nil)
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	tid, ok := taskRef(p.pos[0])
	prText, hasPR := p.opt("--pr")
	pr, err := strconv.ParseInt(strings.TrimPrefix(prText, "#"), 10, 64)
	until, hasUntil, ok2 := optTimeout(p, "--until")
	if !ok || !ok2 || !hasPR || err != nil || pr <= 0 || p.flags["--ci"] && p.flags["--close"] {
		return 0, false
	}
	on := "ci"
	if p.flags["--close"] {
		on = "close"
	}
	repo, given := p.opt("--repo")
	if !given {
		if repo = originRepo(); repo == "" {
			fmt.Println("hl task wait: this checkout has no GitHub origin; name the repo with --repo owner/name")
			return 1, true
		}
	}
	mutating(c, "wait")
	if !asking(c, "make a task wait on a PR") {
		return 1, true
	}
	what := fmt.Sprintf("PR #%d CI", pr)
	if on == "close" {
		what = fmt.Sprintf("PR #%d to close", pr)
	}
	params := map[string]any{"owner": "gate", "title": fmt.Sprintf("T%d waits on %s", tid, what), "watch": map[string]any{"repo": repo, "pr": pr, "on": on}}
	if hasUntil {
		params["time_box"] = fmt.Sprintf("%ds", until)
	}
	ok, words, res := c.Written("task.new", params)
	if !ok {
		return said(false, words), true
	}
	gate := num(res, "task")
	ok, words = c.Write("task.edit", map[string]any{"task": tid, "blocked_by": gate})
	if !ok {
		return said(false, fmt.Sprintf("gate T%d is open, but T%d could not be blocked on it: %s", gate, tid, words)), true
	}
	if t, err := c.Call("task.get", obj{"task": tid}); err == nil && str(t, "state") == "doing" {
		if ok, words = c.Write("task.release", map[string]any{"task": tid, "reason": "waits on gate T" + fmt.Sprint(gate)}); !ok {
			return said(false, fmt.Sprintf("T%d waits on gate T%d, but is still doing: %s", tid, gate, words)), true
		}
	}
	return said(true, fmt.Sprintf("T%d waits on gate T%d (%s in %s)", tid, gate, what, repo)), true
}
