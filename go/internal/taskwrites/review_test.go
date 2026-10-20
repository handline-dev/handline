package taskwrites

import (
	osexec "os/exec"
	"strings"
	"testing"
)

// gitIn runs git in repo as a test user, failing the test on an error; it answers the trimmed output.
func gitIn(t *testing.T, repo string) func(args ...string) string {
	return func(args ...string) string {
		t.Helper()
		cmd := osexec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@example.com"}, args...)...)
		cmd.Dir = repo
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %s", args, out)
		}
		return strings.TrimSpace(string(out))
	}
}

// The tasks reportEnv's w works: T1 the founder asked, T2 boss asked, T3 the founder asked of lead.auditor, T4 the founder asked of mgr
// (who has no lead).
var reviewRows = []string{
	"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'w', 'human', 1), (2, 'b', 'w', 'boss', 1), " +
		"(3, 'c', 'lead.auditor', 'human', 1), (4, 'd', 'mgr', 'human', 1)",
}

func TestTheAuditorsOwnDoneAndADoneWithNoLeadCloseAtOnce(t *testing.T) {
	sdb, _, run := reportEnv(t, reviewRows...)
	if got := said(t, run("lead.auditor", "task.done", "d3", `,"task":3,"evidence":"PASS"`)); got != "task T3 done (PASS)" {
		t.Fatalf("the auditor's done is the review: %q", got)
	}
	if got := said(t, run("mgr", "task.done", "d4", `,"task":4,"evidence":"built"`)); got != "task T4 done (built)" {
		t.Fatalf("no lead, so no review, and the reporter is not needed: %q", got)
	}
	if taskState(t, sdb, 3) != "done" || taskState(t, sdb, 4) != "done" {
		t.Fatal("both close")
	}
}

func TestTheReporterReopensADiscardedTask(t *testing.T) {
	sdb, _, run := reportEnv(t, reviewRows...)
	said(t, run("boss", "task.discard", "x1", `,"task":2,"reason":"not now"`))
	said(t, run("boss", "task.reopen", "o1", `,"task":2,"note":"now"`))
	if st := taskState(t, sdb, 2); st != "draft" {
		t.Fatalf("state %s, want draft", st)
	}
}
