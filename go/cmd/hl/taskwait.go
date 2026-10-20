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

// taskWait is hl task wait T<n> (--on <vendor>:<target> | --pr <n> [--repo owner/name]) [--until <what>] [-t <span>]: one task.wait, which
// opens a gate on the target, blocks T<n> on it and, when doing, releases it, so its holder takes other work and T<n> comes back to its
// queue when the gate opens. --pr is github:<origin repo>#<n>; --until defaults to the vendor's first (a PR's CI).
func taskWait(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--on", "--pr", "--repo", "--until", "--timeout"}, nil, map[string]string{"-t": "--timeout"})
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	tid, ok := taskRef(p.pos[0])
	secs, hasSecs, ok2 := optTimeout(p, "--timeout")
	on, hasOn := p.opt("--on")
	prText, hasPR := p.opt("--pr")
	repo, hasRepo := p.opt("--repo")
	if !ok || !ok2 || hasOn == hasPR || hasRepo && !hasPR {
		return 0, false
	}
	vendor, target, _ := strings.Cut(on, ":")
	if hasPR {
		pr, err := strconv.ParseInt(strings.TrimPrefix(prText, "#"), 10, 64)
		if err != nil || pr <= 0 {
			return 0, false
		}
		if !hasRepo {
			if repo = originRepo(); repo == "" {
				fmt.Println("hl task wait: this checkout has no GitHub origin; name the repo with --repo owner/name, or the target with --on")
				return 1, true
			}
		}
		vendor, target = "github", fmt.Sprintf("%s#%d", repo, pr)
	}
	mutating(c, "wait")
	if !asking(c, "make a task wait") {
		return 1, true
	}
	w := map[string]any{"vendor": vendor, "target": target}
	if until, given := p.opt("--until"); given {
		w["until"] = until
	}
	params := map[string]any{"task": tid, "watch": w}
	if hasSecs {
		params["time_box"] = fmt.Sprintf("%ds", secs)
	}
	ok, words := c.Write("task.wait", params)
	return said(ok, words), true
}
