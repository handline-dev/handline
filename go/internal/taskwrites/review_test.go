package taskwrites

import (
	"fmt"
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

func TestADoneByAnAssigneeWithALeadIsAReviewItsLeadOrAuditorCloses(t *testing.T) {
	sdb, _, run := reportEnv(t, reviewRows...)
	said(t, run("w", "task.done", "d1", `,"task":1,"evidence":"built"`))
	said(t, run("w", "task.done", "d2", `,"task":2,"evidence":"built"`))
	for tid := int64(1); tid <= 2; tid++ {
		if st := taskState(t, sdb, tid); st != "review" {
			t.Fatalf("T%d is %s, want review", tid, st)
		}
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests WHERE target = 'human'") != 0 {
		t.Fatal("the founder, T1's reporter, is not asked to accept")
	}
	if got := said(t, run("lead", "task.accept", "a1", `,"task":1`)); got != "task T1 done (built), accepted by lead" {
		t.Fatalf("the lead accepts: %q", got)
	}
	if got := said(t, run("lead.auditor", "task.accept", "a2", `,"task":2`)); got != "task T2 done (built), accepted by lead.auditor" {
		t.Fatalf("the auditor accepts: %q", got)
	}
	for tid := int64(1); tid <= 2; tid++ {
		if st := taskState(t, sdb, tid); st != "done" {
			t.Fatalf("T%d is %s, want done", tid, st)
		}
	}
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

func TestOnlyTheLeadAuditorReporterOrFounderReviews(t *testing.T) {
	_, _, run := reportEnv(t, reviewRows...)
	said(t, run("w", "task.done", "d1", `,"task":1,"evidence":"built"`))
	for _, who := range []string{"w", "boss", "mgr"} {
		if r := run(who, "task.accept", "x"+who, `,"task":1`); !strings.Contains(errText(r), "can only be accepted by its assignee's lead or auditor") {
			t.Fatalf("%s: %v", who, r)
		}
	}
}

func TestAnAcceptedTaskWithAPRWaitsOnItsMerge(t *testing.T) {
	repo := t.TempDir()
	git := gitIn(t, repo)
	git("init", "-q", "-b", "main")
	git("commit", "-q", "--allow-empty", "-m", "merged")
	merge := git("rev-parse", "HEAD")
	sdb, f, run := reportEnv(t, reviewRows...)
	if got := said(t, run("w", "task.edit", "e1", `,"task":1,"pr":12`)); got != "task T1 edited by w: pr '' -> '#12'" {
		t.Fatalf("pr: %q", got)
	}
	said(t, run("w", "task.done", "d1", `,"task":1,"evidence":"built"`))
	if got := said(t, run("lead", "task.accept", "a1", `,"task":1`)); got != "task T1 accepted by lead; it closes when PR #12 merges" {
		t.Fatalf("accept: %q", got)
	}
	if st := taskState(t, sdb, 1); st != "review" {
		t.Fatalf("an accepted task with a PR stays open: %s", st)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND accepted_by = 'lead' AND done_evidence IS NULL") != 1 ||
		count(t, sdb, "SELECT COUNT(*) FROM events WHERE kind = 'accept_task' AND json_extract(payload, '$.pr') = 12") != 1 {
		t.Fatal("the accept is kept, with its PR")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests WHERE target = 'w' AND body = 'task T1 accepted by lead; it closes when PR #12 merges'") != 1 || len(f.notified) != 2 {
		t.Fatalf("the assignee is told, after the edit was: %v", f.notified)
	}
	if r := run("lead.auditor", "task.accept", "a2", `,"task":1`); errText(r) != "task T1 is already accepted by lead - it closes when PR #12 merges" {
		t.Fatalf("a second accept: %v", r)
	}
	if r := run("w", "task.done", "d2", `,"task":1,"evidence":"merged now"`); !strings.Contains(errText(r), "closes when PR #12 merges: mark it done with the merge sha") {
		t.Fatalf("words do not close it: %v", r)
	}
	if got := said(t, run("lead", "task.done", "d3", fmt.Sprintf(`,"task":1,"evidence":%q,"repo":%q`, merge, repo))); got != "task T1 done ("+merge+")" {
		t.Fatalf("the merge closes it, done by the lead: %q", got)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND accepted_by IS NULL AND reported_at IS NULL AND done_via = 'sha'") != 1 {
		t.Fatal("done with the merge sha, the review gone")
	}
}

func TestARejectOrAHandoffDropsAnAccept(t *testing.T) {
	sdb, _, run := reportEnv(t, reviewRows...)
	accept := func(n string) {
		t.Helper()
		said(t, run("w", "task.edit", "e"+n, `,"task":1,"pr":`+n))
		said(t, run("w", "task.done", "d"+n, `,"task":1,"evidence":"built"`))
		said(t, run("lead", "task.accept", "a"+n, `,"task":1`))
	}
	accept("12")
	said(t, run("human", "task.reject", "r1", `,"task":1,"note":"not this way"`))
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND accepted_by IS NULL AND accepted_at IS NULL AND reported_at IS NULL") != 1 {
		t.Fatal("the reject drops the accept with the report")
	}
	accept("13")
	said(t, run("w", "task.handoff", "h1", `,"task":1,"to":"mgr","note":"redo it"`))
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'mgr' AND accepted_by IS NULL AND accepted_at IS NULL AND reported_at IS NULL") != 1 {
		t.Fatal("the new assignee's work is not accepted yet")
	}
}

func TestAHandoffDropsAWaitingReview(t *testing.T) {
	sdb, _, run := reportEnv(t, reviewRows...)
	said(t, run("w", "task.done", "d1", `,"task":1,"evidence":"half"`))
	said(t, run("w", "task.handoff", "h1", `,"task":1,"to":"mgr","note":"finish it"`))
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'mgr' AND opened_by = 'human' AND reported_at IS NULL AND report IS NULL") != 1 {
		t.Fatal("the assignee changes, the reporter stays, the review goes")
	}
	if st := taskState(t, sdb, 1); st == "review" {
		t.Fatal("no longer in review")
	}
	if r := run("lead", "task.accept", "a1", `,"task":1`); errText(r) != "task T1 has no answer waiting - nothing to accept" {
		t.Fatalf("nothing left to accept: %v", r)
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
