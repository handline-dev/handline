// Package gitrun runs git and hands back what it said, the same way for every caller (handline/infra/gitrun.py).
package gitrun

import (
	"bytes"
	"context"
	"errors"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// IntegrationBranch is the branch work is branched from and merged into.
const IntegrationBranch = "main"

// BehindWarn: a branch forked this many commits before main's tip was not branched from main's tip.
const BehindWarn = 100

// Result is what git said, untouched. RC is -1 when git could not be run at all (missing, or past the timeout) and Stderr says why.
type Result struct {
	RC             int
	Stdout, Stderr string
}

// Run runs git in cwd with a 15 second limit.
func Run(cwd string, args ...string) Result { return RunTimeout(cwd, 15*time.Second, args...) }

func RunTimeout(cwd string, limit time.Duration, args ...string) Result {
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = cwd
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	var exit *exec.ExitError
	switch {
	case err == nil:
		return Result{0, out.String(), errb.String()}
	case errors.As(err, &exit) && ctx.Err() == nil:
		return Result{exit.ExitCode(), out.String(), errb.String()}
	case ctx.Err() != nil:
		return Result{-1, "", "git timed out"}
	}
	return Result{-1, "", err.Error()}
}

// PointOriginHeadAtMain makes origin/HEAD name origin/main. True when it was changed; false when it already did, or when
// there is no origin/main to point at.
func PointOriginHeadAtMain(cwd string) bool {
	r := Run(cwd, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
	if r.RC == 0 && strings.TrimSpace(r.Stdout) == "refs/remotes/origin/"+IntegrationBranch {
		return false
	}
	return Run(cwd, "remote", "set-head", "origin", IntegrationBranch).RC == 0
}

// CommitsBehindMain is how many commits main holds that HEAD was forked before; ok is false with no main, no HEAD or no common ancestor.
func CommitsBehindMain(cwd string) (n int, ok bool) {
	base := Run(cwd, "merge-base", "HEAD", IntegrationBranch)
	if base.RC != 0 || strings.TrimSpace(base.Stdout) == "" {
		return 0, false
	}
	out := Run(cwd, "rev-list", "--count", strings.TrimSpace(base.Stdout)+".."+IntegrationBranch)
	n, err := strconv.Atoi(strings.TrimSpace(out.Stdout))
	return n, out.RC == 0 && err == nil
}
