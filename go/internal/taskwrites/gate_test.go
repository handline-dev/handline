package taskwrites

import (
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
)

// A gate is a task owned by gate with what it watches: it asks no one, is doing from the start, and its deadline is 24h unless a time box
// is given. watch is required with owner gate and refused with any other.
func TestTaskNewOpensAGateThatWatchesAPR(t *testing.T) {
	sdb, _, run := reportEnv(t)
	r := run("w", "task.new", "g1", `,"owner":"gate","title":"wait for PR 7's CI","watch":{"repo":"o/r","pr":7,"on":"ci"}`)
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
	if w, err := db.TaskWatch(sdb, tid); err != nil || w == nil || *w != (db.Watch{Repo: "o/r", PR: 7, On: "ci"}) {
		t.Fatalf("%+v %v", w, err)
	}
	if left := int64(count(t, sdb, "SELECT timeout_at FROM tasks WHERE id = 1")) - time.Now().Unix(); left < db.GateDeadlineS-60 || left > db.GateDeadlineS {
		t.Fatalf("deadline in %ds", left)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM requests") != 0 {
		t.Fatal("a gate asks no one")
	}
	for _, c := range []struct{ params, why string }{
		{`,"owner":"gate","title":"x"`, "a gate watches something"},
		{`,"owner":"w","title":"x","watch":{"repo":"o/r","pr":7,"on":"ci"}`, "watch goes with owner gate"},
		{`,"owner":"gate","title":"x","watch":{"repo":"o/r","pr":7,"on":"merge"}`, "watch.on is ci or close"},
		{`,"owner":"gate","title":"x","watch":{"repo":"nope","pr":7,"on":"ci"}`, "watch.repo is a GitHub repo"},
	} {
		r := run("w", "task.new", "g-"+c.why, c.params)
		e, _ := r["error"].(map[string]any)
		if e == nil || !strings.Contains(e["message"].(string), c.why) {
			t.Errorf("%s: %v", c.params, r)
		}
	}
}
