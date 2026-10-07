package gitrun

import (
	"os/exec"
	"strings"
	"testing"
	"time"
)

func repo(t *testing.T) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	dir := t.TempDir()
	for _, args := range [][]string{{"init", "-q", "-b", "main"}, {"-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "x"}} {
		if r := Run(dir, args...); r.RC != 0 {
			t.Fatalf("%v: %+v", args, r)
		}
	}
	return dir
}

func TestRunHandsBackWhatGitSaid(t *testing.T) {
	dir := repo(t)
	if r := Run(dir, "rev-parse", "--abbrev-ref", "HEAD"); r.RC != 0 || strings.TrimSpace(r.Stdout) != "main" {
		t.Fatalf("%+v", r)
	}
	if r := Run(dir, "no-such-command"); r.RC == 0 || r.RC == -1 || r.Stderr == "" {
		t.Fatalf("%+v", r)
	}
	if r := Run(t.TempDir()+"/missing", "status"); r.RC != -1 || r.Stderr == "" {
		t.Fatalf("git could not be run: %+v", r)
	}
	if r := RunTimeout(dir, time.Nanosecond, "status"); r.RC != -1 {
		t.Fatalf("past its limit: %+v", r)
	}
}

func TestCommitsBehindMain(t *testing.T) {
	dir := repo(t)
	Run(dir, "checkout", "-q", "-b", "feature")
	Run(dir, "checkout", "-q", "main")
	Run(dir, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "two")
	Run(dir, "checkout", "-q", "feature")
	if n, ok := CommitsBehindMain(dir); !ok || n != 1 {
		t.Fatalf("%d %v", n, ok)
	}
	if _, ok := CommitsBehindMain(t.TempDir()); ok {
		t.Fatal("no repo is not behind")
	}
}

func TestPointOriginHeadAtMainNeedsAnOriginMain(t *testing.T) {
	if PointOriginHeadAtMain(repo(t)) {
		t.Fatal("changed with no origin/main")
	}
}
