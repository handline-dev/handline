package main

import (
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// gitRepo makes the rig's root a checkout with an origin (a bare repository beside it) that has branch b pushed; it
// returns b's head.
func gitRepo(t *testing.T, r *rig) string {
	t.Helper()
	git := func(dir string, args ...string) string {
		cmd := exec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@localhost"}, args...)...)
		cmd.Dir = dir
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	origin := filepath.Join(t.TempDir(), "origin.git")
	git(r.root, "init", "-q", "--bare", origin)
	git(r.root, "init", "-q", "-b", "main")
	git(r.root, "remote", "add", "origin", origin)
	git(r.root, "commit", "-q", "--allow-empty", "-m", "one")
	git(r.root, "push", "-q", "origin", "main:b")
	git(r.root, "branch", "b")
	return git(r.root, "rev-parse", "b")
}

func TestPRRequestOpensATaskForThePRAgent(t *testing.T) {
	r := agentsRig(t)
	head := gitRepo(t, r)
	calls := r.daemon(map[string]string{"task.new": `{"said":"asked handline.pr","task":77}`})
	out, _, code := r.run(asAgent, "pr", "request", "b", "T7", "--base", "release")
	if code != 0 || out != "asked handline.pr\nT77\n" {
		t.Fatalf("%q exit %d", out, code)
	}
	want := []string{`task.new {"body":"Open a pull request for T7.\nbranch: b\nhead: ` + head +
		`\nbase: release\nasked by: a.one","owner":"handline.pr","title":"PR for T7: b into release"}`}
	if got := sent(calls()); !reflect.DeepEqual(got, want) {
		t.Errorf("\n got  %q\n want %q", got, want)
	}
}

func TestPRRequestRefusesABranchNotPushed(t *testing.T) {
	r := agentsRig(t)
	gitRepo(t, r)
	calls := r.daemon(nil)
	out, _, code := r.run(asAgent, "pr", "request", "nope", "T7")
	if code != 1 || !strings.HasPrefix(out, "refused: origin has no branch nope.") {
		t.Errorf("a branch origin lacks: %q exit %d", out, code)
	}
	cmd := exec.Command("git", "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-q", "--allow-empty", "-m", "two")
	cmd.Dir = r.root
	if err := exec.Command("git", "-C", r.root, "checkout", "-q", "b").Run(); err != nil {
		t.Fatal(err)
	}
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, out)
	}
	out, _, code = r.run(asAgent, "pr", "request", "b", "T7")
	if code != 1 || !strings.HasPrefix(out, "refused: b is at ") || !strings.Contains(out, "Push it first") {
		t.Errorf("a branch ahead of origin: %q exit %d", out, code)
	}
	if got := calls(); len(got) != 0 {
		t.Errorf("asked the daemon: %v", got)
	}
}

func TestPRRequestUsage(t *testing.T) {
	r := newRig(t)
	for _, args := range [][]string{{"pr"}, {"pr", "create"}, {"pr", "request", "b"}, {"pr", "request", "b", "12"}, {"pr", "request", "b", "T1", "--head", "x"}} {
		if out, _, code := r.run(asAgent, args...); code != 2 || out != prUsage+"\n" {
			t.Errorf("%v: %q exit %d", args, out, code)
		}
	}
}
