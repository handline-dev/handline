package gates

import (
	"context"
	"database/sql"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/watch"
)

// vendor answers each Check with what read says.
type vendor struct {
	read func(ctx context.Context, target, until string) (watch.Verdict, string, error)
}

func (vendor) Untils() []string                          { return []string{"ci"} }
func (vendor) Ready(context.Context) (bool, string)      { return true, "" }
func (vendor) Parse(t, _ string) (string, string, error) { return t, t, nil }
func (v vendor) Check(ctx context.Context, target, until string) (watch.Verdict, string, error) {
	return v.read(ctx, target, until)
}

func (s *Sweep) reads(read func(ctx context.Context, target, until string) (watch.Verdict, string, error)) {
	s.Vendors = map[string]watch.Vendor{"test": vendor{read}}
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

// gateRig is a store with gate T1 (on target o/r#7 of vendor test, deadline at 5000) blocking T2, the task bob waits with.
func gateRig(t *testing.T) (*sql.DB, *doneTeller, *Sweep) {
	t.Helper()
	sdb, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sdb.Close() })
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at, watch_vendor, watch_target, watch_until, watch_label, " +
			"timeout_at) VALUES(1, 'wait for CI', 'gate', 'bob', 1, 'bob', 1, 'test', 'o/r#7', 'ci', 'PR #7 CI', 5000)",
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

func lastError(t *testing.T, sdb *sql.DB) string {
	t.Helper()
	w, err := db.TaskWatch(sdb, 1)
	if err != nil || w == nil {
		t.Fatal(w, err)
	}
	return w.Error
}

// A gate opens with its vendor's evidence once the vendor answers passed or failed; the task it blocked is free and its holder is told by
// the ordinary done path. A read that fails leaves it watching, with the reason on it until a read answers again.
func TestTheSweepOpensAGateOnItsVendorsVerdict(t *testing.T) {
	sdb, tell, s := gateRig(t)
	verdict, readErr := watch.Waiting, error(nil)
	s.reads(func(_ context.Context, target, until string) (watch.Verdict, string, error) {
		if target != "o/r#7" || until != "ci" {
			t.Fatalf("read %s %s", target, until)
		}
		if readErr != nil {
			return "", "", readErr
		}
		return verdict, "ci passed", nil
	})
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(s.Pass(context.Background(), 1000))
	if e, _, b := gateState(t, sdb); e != "" || b != 1 {
		t.Fatalf("opened while waiting: %q %d", e, b)
	}
	readErr = errors.New("gh: rate limited")
	must(s.Pass(context.Background(), 1060))
	if e, _, _ := gateState(t, sdb); e != "" || lastError(t, sdb) != "gh: rate limited" {
		t.Fatalf("a failed read: %q %q", e, lastError(t, sdb))
	}
	readErr = nil
	must(s.Pass(context.Background(), 1090))
	if lastError(t, sdb) != "" {
		t.Fatal("a read that answered left the old error")
	}
	verdict = watch.Passed
	must(s.Pass(context.Background(), 1120))
	if e, r, b := gateState(t, sdb); e != "ci passed" || r != "passed" || b != 0 {
		t.Fatalf("%q %q %d", e, r, b)
	}
	if len(tell.done) != 1 || tell.done[0] != 1 {
		t.Fatalf("%v", tell.done)
	}
}

// A target that reads gone watch.GoneReads times in a row opens the gate as failed; any other answer between starts the count again.
func TestAGoneTargetFailsTheGateAfterThreeReadsInARow(t *testing.T) {
	sdb, _, s := gateRig(t)
	answers := []watch.Verdict{watch.Gone, watch.Gone, watch.Waiting, watch.Gone, watch.Gone}
	s.reads(func(context.Context, string, string) (watch.Verdict, string, error) {
		v := answers[0]
		answers = answers[1:]
		return v, "not found", nil
	})
	for i := range 5 {
		if err := s.Pass(context.Background(), int64(1000+i)); err != nil {
			t.Fatal(err)
		}
	}
	if e, _, _ := gateState(t, sdb); e != "" || lastError(t, sdb) != "gone: not found" {
		t.Fatalf("opened before three gone reads in a row: %q %q", e, lastError(t, sdb))
	}
	answers = []watch.Verdict{watch.Gone}
	if err := s.Pass(context.Background(), 1010); err != nil {
		t.Fatal(err)
	}
	if e, r, b := gateState(t, sdb); e != "gone: not found" || r != "failed" || b != 0 {
		t.Fatalf("%q %q %d", e, r, b)
	}
}

// Past its deadline a gate opens "timed out", without reading its target.
func TestAGatePastItsDeadlineOpensTimedOut(t *testing.T) {
	sdb, _, s := gateRig(t)
	s.reads(func(context.Context, string, string) (watch.Verdict, string, error) {
		t.Fatal("read past the deadline")
		return "", "", nil
	})
	if err := s.Pass(context.Background(), 5000); err != nil {
		t.Fatal(err)
	}
	if e, r, b := gateState(t, sdb); e != "timed out" || r != "timed_out" || b != 0 {
		t.Fatalf("%q %q %d", e, r, b)
	}
}

// Run passes on its own clock, and closing stop ends it even while a read of a vendor hangs.
func TestTheSweepStopsWithTheDaemonMidRead(t *testing.T) {
	sdb, _, s := gateRig(t)
	if _, err := sdb.Exec("UPDATE tasks SET timeout_at = ? WHERE id = 1", time.Now().Unix()+3600); err != nil {
		t.Fatal(err)
	}
	reading := make(chan struct{})
	s.reads(func(ctx context.Context, _, _ string) (watch.Verdict, string, error) {
		close(reading)
		<-ctx.Done()
		return "", "", ctx.Err()
	})
	stop, done := make(chan struct{}), make(chan struct{})
	go func() { s.Run(stop); close(done) }()
	<-reading
	close(stop)
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("the sweep did not stop")
	}
	if e, _, _ := gateState(t, sdb); e != "" {
		t.Fatalf("opened on a cut-short read: %q", e)
	}
}
