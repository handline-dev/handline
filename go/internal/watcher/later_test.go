package watcher

import (
	"database/sql"
	"encoding/json"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/stream"
)

// laterRig is a database with open tasks 1 to 3 and a clock the test moves.
type laterRig struct {
	t    *testing.T
	d    *sql.DB
	l    *stream.Log
	w    *Later
	now  int64
	told int
}

func newLaterRig(t *testing.T) *laterRig {
	d, l := rig(t)
	r := &laterRig{t: t, d: d, l: l, now: 1_000}
	for i := 1; i <= 3; i++ {
		r.exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(?, 't', 'human', 'boss', 1)", i)
	}
	r.w = &Later{Now: func() time.Time { return time.Unix(r.now, 0) }}
	return r
}

func (r *laterRig) exec(q string, args ...any) {
	r.t.Helper()
	exec(r.t, r.d, q, args...)
}

// scan is one look of the detector, and the returns it told.
func (r *laterRig) scan() []map[string]any {
	r.t.Helper()
	if err := r.w.Scan(r.d, r.l.Append); err != nil {
		r.t.Fatal(err)
	}
	got, err := r.l.Since(0, []string{"tasks"}, "", 100)
	if err != nil {
		r.t.Fatal(err)
	}
	var out []map[string]any
	for _, e := range got[r.told:] {
		if e.Type != "later_returned" {
			r.t.Fatalf("a %s", e.Type)
		}
		var data map[string]any
		if err := json.Unmarshal(e.Data, &data); err != nil {
			r.t.Fatal(err)
		}
		out = append(out, data)
	}
	r.told = len(got)
	return out
}

func (r *laterRig) held(kind, id string) bool {
	r.t.Helper()
	rows, err := db.LaterRows(r.d)
	if err != nil {
		r.t.Fatal(err)
	}
	for _, l := range rows {
		if l.Kind == kind && l.ID == id {
			return true
		}
	}
	return false
}

// set defers the item until a time, or when a task ends: the other is 0.
func (r *laterRig) set(kind, id string, until, after int64) {
	r.t.Helper()
	var u, a *int64
	if until != 0 {
		u = &until
	}
	if after != 0 {
		a = &after
	}
	if err := db.LaterSet(r.d, kind, id, u, a, r.now); err != nil {
		r.t.Fatal(err)
	}
}

func TestADeferredItemReturnsWhenItsTimePasses(t *testing.T) {
	r := newLaterRig(t)
	r.set("task", "1", 2_000, 0)
	if got := r.scan(); len(got) != 0 || !r.held("task", "1") {
		t.Fatalf("before its time: %v", got)
	}
	r.now = 1_999
	if got := r.scan(); len(got) != 0 || !r.held("task", "1") {
		t.Fatalf("a second before its time: %v", got)
	}
	r.now = 2_000
	got := r.scan()
	if len(got) != 1 || got[0]["why"] != "time" || got[0]["item"].(map[string]any)["id"] != "1" || r.held("task", "1") {
		t.Fatalf("at its time: %v", got)
	}
	if got := r.scan(); len(got) != 0 {
		t.Fatalf("told again: %v", got)
	}
}

func TestADeferredItemReturnsWhenTheTaskItWaitsForIsDoneOrDiscarded(t *testing.T) {
	r := newLaterRig(t)
	r.set("task", "1", 0, 2)
	r.set("task", "3", 0, 2)
	r.set("comment", "7", 0, 3)
	r.exec("INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(7, 1, 'w', '@human?', 1)")
	r.exec("INSERT INTO requests(id, ts, requester, target, body, state, memo_id) VALUES(70, 1, 'w', 'human', '@human?', 'queued', 7)")
	if got := r.scan(); len(got) != 0 {
		t.Fatalf("while the tasks are open: %v", got)
	}
	r.exec("UPDATE tasks SET done_evidence = 'x', done_at = 5 WHERE id = 2")
	got := r.scan()
	if len(got) != 2 || got[0]["why"] != "task_done" || r.held("task", "1") || r.held("task", "3") || !r.held("comment", "7") {
		t.Fatalf("task 2 done: %v", got)
	}
	r.exec("UPDATE tasks SET discarded_by = 'human', discarded_at = 6 WHERE id = 3")
	got = r.scan()
	if len(got) != 1 || got[0]["why"] != "task_discarded" || got[0]["item"].(map[string]any)["kind"] != "comment" || r.held("comment", "7") {
		t.Fatalf("task 3 discarded: %v", got)
	}
}

func TestAClearedDeferralIsNotToldAndAGoneItemIsDroppedQuietly(t *testing.T) {
	r := newLaterRig(t)
	r.set("task", "1", 2_000, 0)
	if _, err := db.LaterClear(r.d, "task", "1"); err != nil {
		t.Fatal(err)
	}
	r.now = 3_000
	if got := r.scan(); len(got) != 0 {
		t.Fatalf("a cleared deferral came back: %v", got)
	}
	r.set("task", "2", 5_000, 0) // its task ends first
	r.set("comment", "9", 5_000, 0)
	r.set("machine", "disk", 5_000, 0)
	r.exec("UPDATE tasks SET done_evidence = 'x', done_at = 5 WHERE id = 2")
	if got := r.scan(); len(got) != 0 || r.held("task", "2") || r.held("comment", "9") || r.held("machine", "disk") {
		t.Fatalf("a gone item was told or kept: %v", got)
	}
}

// Only the disk is a machine need: a deferral of another machine id is dropped as gone while the disk need stands.
func TestAMachineDeferralOfAnythingButTheDiskIsDroppedQuietly(t *testing.T) {
	r := newLaterRig(t)
	r.exec("INSERT INTO cursors(name, row_id, updated) VALUES('disk_full_since', 9, 9)")
	r.set("machine", "disk", 5_000, 0)
	r.set("machine", "memory", 5_000, 0)
	if got := r.scan(); len(got) != 0 || !r.held("machine", "disk") || r.held("machine", "memory") {
		t.Fatalf("told %v; disk held %v, memory held %v", got, r.held("machine", "disk"), r.held("machine", "memory"))
	}
}

// The database does not change when a time passes, so the watch is woken by the signature alone.
func TestTheSignatureChangesWhenADeferralsTimePasses(t *testing.T) {
	r := newLaterRig(t)
	sig := LaterSignature(r.d)
	quiet := sig()
	r.set("task", "1", time.Now().Unix()+3600, 0)
	if sig() != quiet {
		t.Fatalf("a deferral still to come changed the signature")
	}
	r.set("task", "2", time.Now().Unix()-1, 0)
	if sig() == quiet {
		t.Fatalf("a deferral whose time passed left the signature as it was")
	}
}

// The founder defers an item again after the scan read its row: the new deferral stands, and nothing is told.
func TestADeferralReplacedDuringAScanIsNeitherDeletedNorTold(t *testing.T) {
	r := newLaterRig(t)
	r.set("task", "1", 2_000, 0)
	r.now = 2_000
	r.w.afterRead = func() { r.set("task", "1", 9_000, 0) }
	if got := r.scan(); len(got) != 0 || !r.held("task", "1") {
		t.Fatalf("told %v, held %v", got, r.held("task", "1"))
	}
}
