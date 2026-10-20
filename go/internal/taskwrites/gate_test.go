package taskwrites

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/watch"
)

// loggedIn makes the github vendor read as set up without running gh.
func loggedIn(t *testing.T) {
	was := watch.Vendors["github"]
	watch.Vendors["github"] = &watch.GitHub{Run: func(context.Context, ...string) ([]byte, error) { return nil, nil }}
	t.Cleanup(func() { watch.Vendors["github"] = was })
}

// A gate is a task owned by gate with what it watches: it asks no one, is doing from the start, and its deadline is 24h unless a time box
// is given. watch is required with owner gate and refused with any other; a vendor refuses a watch it cannot take.
func TestTaskNewOpensAGateThatWatchesAVendorsTarget(t *testing.T) {
	loggedIn(t)
	sdb, _, run := reportEnv(t)
	r := run("w", "task.new", "g1", `,"owner":"gate","title":"wait for PR 7's CI","watch":{"vendor":"github","target":"o/r#7"}`)
	if r["error"] != nil {
		t.Fatalf("%v", r["error"])
	}
	if s := said(t, r); !strings.Contains(s, "watching PR #7 CI") {
		t.Fatal(s)
	}
	tid := int64(r["result"].(map[string]any)["task"].(float64))
	if st := taskState(t, sdb, tid); st != "doing" {
		t.Fatalf("state %s", st)
	}
	w, err := db.TaskWatch(sdb, tid)
	if err != nil || w == nil || w.Since < time.Now().Unix()-60 {
		t.Fatalf("%+v %v", w, err)
	}
	if w.Since = 0; *w != (db.Watch{Vendor: "github", Target: "o/r#7", Until: "ci", Label: "PR #7 CI"}) {
		t.Fatalf("%+v", w)
	}
	if left := int64(count(t, sdb, "SELECT timeout_at FROM tasks WHERE id = 1")) - time.Now().Unix(); left < db.GateDeadlineS-60 || left > db.GateDeadlineS {
		t.Fatalf("deadline in %ds", left)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests") != 0 {
		t.Fatal("a gate asks no one")
	}
	for _, c := range []struct{ params, why string }{
		{`,"owner":"gate","title":"x"`, "a gate watches something"},
		{`,"owner":"w","title":"x","watch":{"vendor":"github","target":"o/r#7"}`, "watch goes with owner gate"},
		{`,"owner":"gate","title":"x","watch":{"vendor":"github","target":"o/r#7","until":"merged"}`, `github waits for ci, closed, done, not "merged"`},
		{`,"owner":"gate","title":"x","watch":{"vendor":"github","target":"nope"}`, "a github target is owner/name#N"},
		{`,"owner":"gate","title":"x","watch":{"vendor":"jira","target":"ENG-7"}`, `no vendor "jira"`},
	} {
		r := run("w", "task.new", "g-"+c.why, c.params)
		e, _ := r["error"].(map[string]any)
		if e == nil || !strings.Contains(e["message"].(string), c.why) {
			t.Errorf("%s: %v", c.params, r)
		}
	}
}

// task.wait opens the gate, blocks the task on it and releases it when doing, in one write: its holder takes other work, and the wait is
// the holder's own bookkeeping, so its lead is not told.
func TestTaskWaitBlocksTheTaskOnANewGateAndReleasesIt(t *testing.T) {
	loggedIn(t)
	sdb, _, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at) VALUES(9, 'ship', 'w', 'boss', 1, 'boss', 5)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body, delivered_at) VALUES(20, 'boss', 'w', 'task', 'landed', 1, 'x', 6)",
		"UPDATE requests SET queue_order = 1 WHERE id = 20",
		"INSERT INTO task_requests(task_id, request_id) VALUES(9, 20)")
	r := run("w", "task.wait", "w1", `,"task":9,"watch":{"vendor":"github","target":"o/r#7","until":"closed"},"time_box":"2h"`)
	if r["error"] != nil {
		t.Fatalf("%v", r["error"])
	}
	res := r["result"].(map[string]any)
	gate := int64(res["gate"].(float64))
	if s := res["said"].(string); !strings.HasPrefix(s, "T9 waits on gate T10 (watching PR #7 to close); task T9 released by w: waits on gate T10") {
		t.Fatal(s)
	}
	if bs, err := db.TaskBlockers(sdb, 9); err != nil || len(bs) != 1 || bs[0].ID != gate {
		t.Fatalf("%v %v", bs, err)
	}
	if st := taskState(t, sdb, 9); st != "queued" {
		t.Fatalf("T9 is %s", st)
	}
	if left := int64(count(t, sdb, "SELECT timeout_at - sent_at FROM tasks WHERE id = 10")); left != 7200 {
		t.Fatalf("gate time box %ds", left)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests WHERE body LIKE '%now waits on%'") != 0 {
		t.Fatal("the lead was told of a wait on a gate")
	}
	// a queued task only waits; a refusal opens no gate
	if r := run("w", "task.wait", "w2", `,"task":9,"watch":{"vendor":"github","target":"o/r#8"}`); r["error"] != nil || strings.Contains(said(t, r), "released") {
		t.Fatalf("%v", r)
	}
	for _, c := range []struct{ params, why string }{
		{`,"task":99,"watch":{"vendor":"github","target":"o/r#7"}`, "no task T99"},
		{`,"task":9`, "watch is {vendor, target, until}"},
		{`,"task":9,"watch":{"vendor":"github","target":"o/r"}`, "a github target is owner/name#N"},
	} {
		if e := errText(run("w", "task.wait", "w-"+c.why, c.params)); !strings.Contains(e, c.why) {
			t.Errorf("%s: %q", c.params, e)
		}
	}
	if n := count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE owner = 'gate'"); n != 2 {
		t.Fatalf("%d gates", n)
	}
}
