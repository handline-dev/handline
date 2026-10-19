package gates

import (
	"context"
	"database/sql"
	"errors"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/taskwrites"
)

func TestAGateOpensOnWhatItsPRShows(t *testing.T) {
	ci, closing := db.Watch{On: "ci"}, db.Watch{On: "close"}
	ok, fail, running := Check{Name: "go", Success: true}, Check{Name: "apps"}, Check{Name: "e2e", Pending: true}
	for _, c := range []struct {
		w                db.Watch
		pr               PR
		evidence, result string
	}{
		{ci, PR{State: "OPEN", Checks: []Check{ok, ok}}, "ci passed", "passed"},
		{ci, PR{State: "OPEN", Checks: []Check{ok, fail}}, "ci failed: apps", "failed"},
		{ci, PR{State: "OPEN", Checks: []Check{fail, running}}, "", ""},
		{ci, PR{State: "OPEN"}, "", ""}, // no check has started yet
		{ci, PR{State: "MERGED", MergeCommit: "abc"}, "merged abc", "merged"},
		{closing, PR{State: "OPEN", Checks: []Check{ok}}, "", ""},
		{closing, PR{State: "CLOSED"}, "closed unmerged", "closed"},
		{closing, PR{State: "MERGED", MergeCommit: "def"}, "merged def", "merged"},
	} {
		if e, r := Opens(c.w, &c.pr); e != c.evidence || r != c.result {
			t.Errorf("%+v on %s: %q %q, want %q %q", c.pr, c.w.On, e, r, c.evidence, c.result)
		}
	}
}

func TestGhOutputReadsAsAPR(t *testing.T) {
	out := `{"mergeCommit":null,"state":"OPEN","statusCheckRollup":[` +
		`{"__typename":"CheckRun","conclusion":"SUCCESS","name":"apps","status":"COMPLETED"},` +
		`{"__typename":"CheckRun","conclusion":"","name":"go","status":"IN_PROGRESS"},` +
		`{"__typename":"CheckRun","conclusion":"FAILURE","name":"lint","status":"COMPLETED"},` +
		`{"__typename":"StatusContext","context":"deploy","state":"PENDING"}]}`
	pr, err := ParseGH([]byte(out))
	if err != nil {
		t.Fatal(err)
	}
	want := []Check{{"apps", false, true}, {"go", true, false}, {"lint", false, false}, {"deploy", true, false}}
	if pr.State != "OPEN" || pr.MergeCommit != "" || len(pr.Checks) != 4 {
		t.Fatalf("%+v", pr)
	}
	for i, c := range want {
		if pr.Checks[i] != c {
			t.Errorf("check %d: %+v, want %+v", i, pr.Checks[i], c)
		}
	}
	if pr, _ := ParseGH([]byte(`{"mergeCommit":{"oid":"984c657"},"state":"MERGED","statusCheckRollup":[]}`)); pr.MergeCommit != "984c657" {
		t.Fatalf("%+v", pr)
	}
}

type doneTeller struct {
	taskwrites.Teller
	done []int64
}

func (d *doneTeller) Done(tid int64, _, _ string, _ []db.Notice, _ []db.InboxFile, finished bool) {
	if finished {
		d.done = append(d.done, tid)
	}
}

// gateRig is a store with gate T1 (on PR 7's CI, deadline at 5000) blocking T2, the task bob waits with.
func gateRig(t *testing.T) (*sql.DB, *doneTeller, *Sweep) {
	t.Helper()
	sdb, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sdb.Close() })
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at, watch_repo, watch_pr, watch_on, timeout_at) " +
			"VALUES(1, 'wait for CI', 'gate', 'bob', 1, 'bob', 1, 'o/r', 7, 'ci', 5000)",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(2, 'ship it', 'bob', 'human', 1, 'human')",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(2, 1, 1)",
	} {
		if _, err := sdb.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	tell := &doneTeller{}
	return sdb, tell, &Sweep{DB: sdb, Home: t.TempDir(), Tell: tell}
}

func gateState(t *testing.T, sdb *sql.DB) (evidence, result string, blocked int) {
	t.Helper()
	var e, r sql.NullString
	if err := sdb.QueryRow("SELECT done_evidence, watch_result FROM tasks WHERE id = 1").Scan(&e, &r); err != nil {
		t.Fatal(err)
	}
	bs, err := db.TaskBlockers(sdb, 2)
	if err != nil {
		t.Fatal(err)
	}
	return e.String, r.String, len(bs)
}

// A gate whose PR's CI finished is done in fixed words; the task it blocked is free and its holder is told by the ordinary done path.
// It is looked at once a minute, and a PR that cannot be read leaves it watching.
func TestTheSweepOpensAGateWhenItsCIFinishes(t *testing.T) {
	sdb, tell, s := gateRig(t)
	pr := &PR{State: "OPEN", Checks: []Check{{Name: "go", Pending: true}}}
	var reads int
	s.Fetch = func(_ context.Context, repo string, n int64) (*PR, error) {
		reads++
		if repo != "o/r" || n != 7 {
			t.Fatalf("read %s #%d", repo, n)
		}
		if pr == nil {
			return nil, errors.New("gh: rate limited")
		}
		return pr, nil
	}
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(s.Run(sdb, 1000))
	if e, _, b := gateState(t, sdb); e != "" || b != 1 {
		t.Fatalf("opened while its CI runs: %q %d", e, b)
	}
	pr = &PR{State: "OPEN", Checks: []Check{{Name: "go", Success: true}}}
	must(s.Run(sdb, 1030))
	if reads != 1 {
		t.Fatalf("looked again within the minute: %d reads", reads)
	}
	pr = nil
	must(s.Run(sdb, 1060))
	if e, _, _ := gateState(t, sdb); e != "" {
		t.Fatal("a PR that cannot be read opened the gate")
	}
	pr = &PR{State: "OPEN", Checks: []Check{{Name: "go", Success: true}}}
	must(s.Run(sdb, 1120))
	if e, r, b := gateState(t, sdb); e != "ci passed" || r != "passed" || b != 0 {
		t.Fatalf("%q %q %d", e, r, b)
	}
	if len(tell.done) != 1 || tell.done[0] != 1 {
		t.Fatalf("%v", tell.done)
	}
}

// Past its deadline a gate opens "timed out", without reading its PR.
func TestAGatePastItsDeadlineOpensTimedOut(t *testing.T) {
	sdb, _, s := gateRig(t)
	s.Fetch = func(context.Context, string, int64) (*PR, error) { t.Fatal("read past the deadline"); return nil, nil }
	if err := s.Run(sdb, 5000); err != nil {
		t.Fatal(err)
	}
	if e, r, b := gateState(t, sdb); e != "timed out" || r != "timed_out" || b != 0 {
		t.Fatalf("%q %q %d", e, r, b)
	}
}
