package main

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
)

// gh runs the GitHub CLI and answers its stdout; tests replace it.
var gh = func(args ...string) ([]byte, error) {
	return exec.Command("gh", args...).Output()
}

// land is `hl land <PR>`: the agents' merge. It merges the PR only when every CI check on its head is green (passed, or skipped by
// the CI plan), and refuses naming each check that failed or is still running. The repo has no branch protection, so GitHub marks
// no check required: every check counts. A person merging by hand is not stopped by this.
func land(_ *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, nil, nil)
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	pr, err := strconv.Atoi(strings.TrimPrefix(p.pos[0], "#"))
	if err != nil || pr <= 0 {
		return 0, false
	}
	why, err := landRefusal(pr)
	if err != nil {
		fmt.Printf("hl land: %v\n", err)
		return 1, true
	}
	if why != "" {
		fmt.Printf("not merged: %s\n", why)
		return 1, true
	}
	return 0, true
}

// landRefusal merges PR pr and answers "" or says why it did not.
func landRefusal(pr int) (string, error) {
	n := strconv.Itoa(pr)
	out, err := gh("pr", "view", n, "--json", "headRefOid,state")
	if err != nil {
		return "", fmt.Errorf("gh pr view %d: %v", pr, ghErr(err))
	}
	var view struct{ HeadRefOid, State string }
	if err := json.Unmarshal(out, &view); err != nil {
		return "", fmt.Errorf("gh pr view %d: %v", pr, err)
	}
	if view.State != "OPEN" {
		return fmt.Sprintf("#%d is %s", pr, strings.ToLower(view.State)), nil
	}
	// gh exits non-zero while a check failed or runs; its JSON still lists them all, so only output that is not JSON is an error.
	out, err = gh("pr", "checks", n, "--json", "name,bucket")
	var checks []struct{ Name, Bucket string }
	if jerr := json.Unmarshal(out, &checks); jerr != nil {
		if err != nil && strings.Contains(ghErr(err), "no checks reported") {
			return fmt.Sprintf("#%d has no CI checks: CI has not run on it", pr), nil
		}
		return "", fmt.Errorf("gh pr checks %d: %v", pr, ghErr(err))
	}
	var bad []string
	for _, c := range checks {
		if c.Bucket != "pass" && c.Bucket != "skipping" {
			bad = append(bad, fmt.Sprintf("%s (%s)", c.Name, c.Bucket))
		}
	}
	switch {
	case len(checks) == 0:
		return fmt.Sprintf("#%d has no CI checks: CI has not run on it", pr), nil
	case len(bad) > 0:
		return fmt.Sprintf("#%d CI is not green: %s", pr, strings.Join(bad, ", ")), nil
	}
	// The checks are those of this head: a push since then is not merged unchecked.
	if _, err := gh("pr", "merge", n, "--merge", "--match-head-commit", view.HeadRefOid); err != nil {
		return "", fmt.Errorf("gh pr merge %d: %v", pr, ghErr(err))
	}
	fmt.Printf("merged #%d at %s\n", pr, view.HeadRefOid)
	return "", nil
}

// ghErr is what gh said on stderr when it failed, else the error itself.
func ghErr(err error) string {
	if ee, ok := err.(*exec.ExitError); ok && len(ee.Stderr) > 0 {
		return strings.TrimSpace(string(ee.Stderr))
	}
	return err.Error()
}
