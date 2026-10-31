package watch

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

// gh answers each run of gh with out (or err), keeping the args it was run with.
func gh(out string, err error, args *[]string) *GitHub {
	return &GitHub{
		Run: func(_ context.Context, a ...string) ([]byte, error) {
			*args = a
			return []byte(out), err
		},
		Now: func() time.Time { return time.Date(2026, 10, 20, 12, 0, 0, 0, time.UTC) },
	}
}

func TestAGitHubTargetIsOwnerNameAndNumber(t *testing.T) {
	g := &GitHub{}
	for _, c := range []struct{ target, until, label, err string }{
		{"handline-dev/handline#418", "ci", "PR #418 CI", ""},
		{"o/r#7", "closed", "PR #7 to close", ""},
		{"o/r#12", "done", "issue #12 done", ""},
		{"o/r", "ci", "", "owner/name#N"},
		{"o/r#0", "ci", "", "owner/name#N"},
		{"r#7", "ci", "", "owner/name#N"},
	} {
		canon, label, err := g.Parse(c.target, c.until)
		if c.err != "" {
			if err == nil || !strings.Contains(err.Error(), c.err) {
				t.Errorf("%s: %v", c.target, err)
			}
			continue
		}
		if err != nil || canon != c.target || label != c.label {
			t.Errorf("%s %s: %q %q %v", c.target, c.until, canon, label, err)
		}
	}
}

func TestPRChecksReadAsACIVerdict(t *testing.T) {
	ok := `{"__typename":"CheckRun","name":"go","status":"COMPLETED","conclusion":"SUCCESS"}`
	skipped := `{"__typename":"CheckRun","name":"docs","status":"COMPLETED","conclusion":"SKIPPED"}`
	failed := `{"__typename":"CheckRun","name":"apps","status":"COMPLETED","conclusion":"FAILURE"}`
	running := `{"__typename":"CheckRun","name":"e2e","status":"IN_PROGRESS","conclusion":""}`
	pending := `{"__typename":"StatusContext","context":"deploy","state":"PENDING"}`
	status := `{"__typename":"StatusContext","context":"deploy","state":"SUCCESS"}`
	pr := func(state, commit string, checks ...string) string {
		return `{"state":"` + state + `","commits":[{"committedDate":"` + commit + `"}],"statusCheckRollup":[` + strings.Join(checks, ",") + `]}`
	}
	t1, t2 := "2026-10-20T11:50:00Z", "2026-10-20T11:51:00Z"
	run := func(name, conclusion, started string) string {
		status := "COMPLETED"
		if conclusion == "" {
			status = "IN_PROGRESS"
		}
		return `{"__typename":"CheckRun","name":"` + name + `","status":"` + status + `","conclusion":"` + conclusion + `","startedAt":"` + started + `"}`
	}
	queued := func(name string) string {
		return `{"__typename":"CheckRun","name":"` + name + `","status":"QUEUED","conclusion":"","startedAt":"0001-01-01T00:00:00Z"}`
	}
	cancelledQueued := func(name string) string { // cancelled before it ever started
		return `{"__typename":"CheckRun","name":"` + name + `","status":"COMPLETED","conclusion":"CANCELLED","startedAt":"0001-01-01T00:00:00Z"}`
	}
	inFlow := func(workflow, name, conclusion, started string) string {
		return `{"__typename":"CheckRun","workflowName":"` + workflow + `","name":"` + name + `","status":"COMPLETED","conclusion":"` + conclusion + `","startedAt":"` + started + `"}`
	}
	old, fresh := "2026-10-20T11:00:00Z", "2026-10-20T11:55:00Z"
	early, now := time.Date(2026, 10, 20, 10, 0, 0, 0, time.UTC), time.Date(2026, 10, 20, 12, 0, 0, 0, time.UTC)
	for _, c := range []struct {
		out      string
		since    time.Time
		verdict  Verdict
		evidence string
	}{
		{pr("OPEN", fresh, ok, skipped, status), early, Passed, "ci passed"},
		{pr("OPEN", fresh, ok, failed), early, Failed, "ci failed: apps"},
		{pr("OPEN", fresh, failed, running), early, Waiting, ""},
		{pr("OPEN", fresh, ok, pending), early, Waiting, ""},
		{pr("MERGED", fresh, ok), early, Passed, "ci passed"}, // CI finished before the merge
		{pr("CLOSED", fresh, running), early, Failed, "closed before CI"},
		{pr("OPEN", fresh), early, Waiting, ""},              // no check has started yet
		{pr("OPEN", old), early, Passed, "no CI on this PR"}, // and none 10 minutes after the head commit
		{pr("OPEN", old), now, Waiting, ""},                  // nor from the watch's start, when that is later
		{pr("MERGED", fresh), early, Failed, "closed before CI"},
		{pr("OPEN", fresh, run("pr-rules", "CANCELLED", t1), run("pr-rules", "SUCCESS", t2), ok), early, Passed, "ci passed"}, // cancelled, then run again
		{pr("OPEN", fresh, run("pr-rules", "SUCCESS", t2), run("pr-rules", "CANCELLED", t1), ok), early, Passed, "ci passed"}, // whatever order the rollup lists them in
		{pr("OPEN", fresh, run("pr-rules", "CANCELLED", t1), ok), early, Failed, "ci failed: pr-rules"},                       // cancelled with no newer run is not green
		{pr("OPEN", fresh, run("pr-rules", "SUCCESS", t1), run("pr-rules", "FAILURE", t2)), early, Failed, "ci failed: pr-rules"},
		{pr("OPEN", fresh, run("pr-rules", "FAILURE", t1), run("pr-rules", "", t2)), early, Waiting, ""},             // the rerun is still going
		{pr("OPEN", fresh, run("pr-rules", "FAILURE", t1), queued("pr-rules")), early, Waiting, ""},                  // a queued rerun has no start time yet
		{pr("OPEN", fresh, queued("pr-rules"), run("pr-rules", "FAILURE", t1)), early, Waiting, ""},                  // in either order
		{pr("OPEN", fresh, cancelledQueued("pr-rules"), run("pr-rules", "SUCCESS", t1)), early, Passed, "ci passed"}, // a run cancelled while queued is the older one
		{pr("OPEN", fresh, run("pr-rules", "SUCCESS", t1), cancelledQueued("pr-rules")), early, Passed, "ci passed"},
		{pr("OPEN", fresh, inFlow("a", "test", "FAILURE", t1), inFlow("b", "test", "SUCCESS", t2)), early, Failed, "ci failed: test"}, // the same name in another workflow is another check
		{pr("OPEN", fresh, failed, run("pr-rules", "CANCELLED", t1), run("pr-rules", "SUCCESS", t2)), early, Failed, "ci failed: apps"},
	} {
		var args []string
		v, e, err := gh(c.out, nil, &args).Check(context.Background(), "o/r#7", "ci", c.since)
		if err != nil || v != c.verdict || e != c.evidence {
			t.Errorf("%s: %s %q %v, want %s %q", c.out, v, e, err, c.verdict, c.evidence)
		}
		if strings.Join(args[:5], " ") != "pr view 7 -R o/r" {
			t.Errorf("ran gh %v", args)
		}
	}
}

func TestAPRCloseAndAnIssueDoneReadAsVerdicts(t *testing.T) {
	for _, c := range []struct {
		until, out string
		verdict    Verdict
		evidence   string
	}{
		{"closed", `{"state":"MERGED","mergeCommit":{"oid":"984c657"}}`, Passed, "merged 984c657"},
		{"closed", `{"state":"CLOSED","mergeCommit":null}`, Failed, "closed unmerged"},
		{"closed", `{"state":"OPEN","mergeCommit":null}`, Waiting, ""},
		{"done", `{"state":"CLOSED","stateReason":"COMPLETED"}`, Passed, "closed as completed"},
		{"done", `{"state":"CLOSED","stateReason":"NOT_PLANNED"}`, Failed, "closed as not planned"},
		{"done", `{"state":"OPEN","stateReason":""}`, Waiting, ""},
	} {
		var args []string
		v, e, err := gh(c.out, nil, &args).Check(context.Background(), "o/r#7", c.until, time.Time{})
		if err != nil || v != c.verdict || e != c.evidence {
			t.Errorf("%s %s: %s %q %v", c.until, c.out, v, e, err)
		}
	}
}

// A repo or number GitHub cannot resolve is gone; any other failure is a read that did not answer.
func TestAMissingTargetIsGoneAndAnyOtherFailureAnError(t *testing.T) {
	var args []string
	v, e, err := gh("", errors.New("gh pr: GraphQL: Could not resolve to a PullRequest with the number of 7."), &args).Check(context.Background(), "o/r#7", "ci", time.Time{})
	if err != nil || v != Gone || !strings.Contains(e, "Could not resolve") {
		t.Fatalf("%s %q %v", v, e, err)
	}
	if _, _, err := gh("", errors.New("gh pr: HTTP 502"), &args).Check(context.Background(), "o/r#7", "ci", time.Time{}); err == nil {
		t.Fatal("a 502 was a verdict")
	}
}

func TestAWatchIsParsedWithoutReadingAndReadyAsksTheVendor(t *testing.T) {
	var args []string
	g := gh("", errors.New("not logged in"), &args)
	defer func(was Vendor) { Vendors["github"] = was }(Vendors["github"])
	Vendors["github"] = g
	for _, c := range []struct{ vendor, target, until, err string }{
		{"gitlab", "g/p!1", "", `no vendor "gitlab"`},
		{"github", "o/r#7", "merged", `github waits for ci, closed, done, not "merged"`},
		{"github", "o/r", "", "owner/name#N"},
	} {
		if _, err := Parse(c.vendor, c.target, c.until); err == nil || !strings.Contains(err.Error(), c.err) {
			t.Errorf("%+v: %v", c, err)
		}
	}
	if args != nil {
		t.Fatalf("ran gh %v for a refusal", args)
	}
	w, err := Parse("github", "o/r#7", "")
	if err != nil || w.Vendor != "github" || w.Target != "o/r#7" || w.Until != "ci" || w.Label != "PR #7 CI" {
		t.Fatalf("%+v %v", w, err)
	}
	if err := Ready(context.Background(), w); err == nil || err.Error() != "github is not set up: gh is not logged in: run gh auth login" {
		t.Fatal(err)
	}
	g.Run = func(context.Context, ...string) ([]byte, error) { return nil, nil }
	if err := Ready(context.Background(), w); err != nil {
		t.Fatal(err)
	}
}
