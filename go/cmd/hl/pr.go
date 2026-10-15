package main

import (
	"fmt"
	"os/exec"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/session"
)

const prUsage = "usage: hl pr request <branch> T<n> [--base <ref>]"

// pr is `hl pr request <branch> T<n> [--base <ref>]`: only session.PRAgent opens pull requests, so a builder asks it with a
// task carrying the branch, its pushed head, the task the work is for and the base. A branch whose head origin does not
// have is refused before anything is asked.
func pr(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 || args[0] != "request" {
		fmt.Println(prUsage)
		return 2, true
	}
	p, ok := parse(args[1:], []string{"--base"}, nil, nil)
	var task int
	if ok && len(p.pos) == 2 {
		task, ok = taskRef(p.pos[1])
	}
	if !ok || len(p.pos) != 2 || p.pos[0] == "" {
		fmt.Println(prUsage)
		return 2, true
	}
	branch := p.pos[0]
	base, given := p.opt("--base")
	if !given {
		base = "main"
	}
	c.NoteVerbChain([]string{"pr", "request"})
	if !asking(c, "ask for a pull request") {
		return 1, true
	}
	sha, why := pushedHead(branch)
	if why != "" {
		fmt.Println(why)
		return 1, true
	}
	body := fmt.Sprintf("Open a pull request for T%d.\nbranch: %s\nhead: %s\nbase: %s\nasked by: %s",
		task, branch, sha, base, c.AskingAgent())
	done, msg, got := c.Written("task.new", map[string]any{"owner": session.PRAgent,
		"title": fmt.Sprintf("PR for T%d: %s into %s", task, branch, base), "body": body})
	fmt.Println(msg)
	if !done {
		return 1, true
	}
	fmt.Printf("T%v\n", got["task"])
	return 0, true
}

// pushedHead is the branch's head commit as origin has it, or why it cannot be asked for: origin has no such branch, or
// the local branch has commits origin does not.
func pushedHead(branch string) (string, string) {
	out, err := exec.Command("git", "ls-remote", "--heads", "origin", "refs/heads/"+branch).Output()
	if err != nil {
		return "", fmt.Sprintf("cannot read origin's branches: %v", err)
	}
	remote, _, _ := strings.Cut(strings.TrimSpace(string(out)), "\t")
	if remote == "" {
		return "", fmt.Sprintf("refused: origin has no branch %s. Push it first: git push -u origin %s", branch, branch)
	}
	local, err := exec.Command("git", "rev-parse", "--verify", "--quiet", "refs/heads/"+branch).Output()
	if head := strings.TrimSpace(string(local)); err == nil && head != remote {
		return "", fmt.Sprintf("refused: %s is at %s here but at %s on origin. Push it first: git push origin %s",
			branch, head[:min(len(head), 12)], remote[:min(len(remote), 12)], branch)
	}
	return remote, ""
}
