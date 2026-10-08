package lifecycle

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/store"
)

func openDB(t *testing.T) db.Q {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return d
}

func newRec(t *testing.T, seen map[string]session.Observed) (*Reconciler, *[]string) {
	var logs []string
	_ = os.Stdout
	r := &Reconciler{Q: openDB(t), Log: func(s string) { logs = append(logs, s) }}
	r.Ports = ReconcilerPorts{
		Agents:           func() []string { return []string{"a"} },
		Observe:          func([]string, map[string]string) map[string]session.Observed { return seen },
		NoteSeen:         func(map[string]session.Observed, time.Time) {},
		RefreshView:      func(db.Q, string) {},
		StillDrawn:       func(string) bool { return false },
		ResumableSession: func(db.Q, string) string { return "s1" },
		Now:              func() int64 { return 5000 },
	}
	return r, &logs
}

func ptr[T any](v T) *T { return &v }

func TestAHandoffPastItsDeadlineDegradesItsAgent(t *testing.T) {
	r, _ := newRec(t, nil)
	if _, err := r.Q.Exec("INSERT INTO handoff(id, agent, trigger_kind, step, deadline, state, updated) VALUES('h','a','x','requested',4000,'in_flight',1)"); err != nil {
		t.Fatal(err)
	}
	acts, err := r.ExpireHandoffs()
	if err != nil || len(acts) != 1 || !strings.Contains(acts[0], "timed out at step 'requested'") {
		t.Fatalf("%v %v", acts, err)
	}
	row, _ := db.GetAgentState(r.Q, "a")
	if row.State != states.Degraded || row.WakeAt.Int64 != 5000+states.DegradedRetryS || row.ThrottleCount != 1 {
		t.Fatalf("%+v", row)
	}
	if left, _ := db.InFlightHandoffs(r.Q); len(left) != 0 {
		t.Fatal("still in flight")
	}
}

func TestADeadTerminalDropsTheHandleButKeepsTheWorktree(t *testing.T) {
	r, _ := newRec(t, map[string]session.Observed{"a": {TerminalAlive: ptr(false)}})
	db.SetTerminal(r.Q, "a", "term1", "/wt")
	db.SetAgentState(r.Q, "a", states.Running, 0, "")
	var acts []string
	if err := r.Sweep(&acts); err != nil {
		t.Fatal(err)
	}
	row, _ := db.GetAgentState(r.Q, "a")
	if row.Terminal.String != "" || row.Worktree.String != "/wt" || !row.ClosedAt.Valid || row.ResumeSession.String != "s1" || row.State != states.Idle {
		t.Fatalf("%+v", row)
	}
	if len(acts) != 1 || !strings.Contains(acts[0], "recorded as closed (resume s1)") {
		t.Fatalf("%v", acts)
	}
}

func TestASessionWithNoProcessIsRecordedClosedUnlessTheScreenDrawsOne(t *testing.T) {
	seen := map[string]session.Observed{"a": {TerminalAlive: ptr(true), SessionSeen: true}}
	r, _ := newRec(t, seen)
	db.SetTerminal(r.Q, "a", "term1", "/wt")
	r.Ports.StillDrawn = func(string) bool { return true }
	var acts []string
	if err := r.Sweep(&acts); err != nil || len(acts) != 0 {
		t.Fatalf("%v %v", acts, err)
	}
	r.Ports.StillDrawn = func(string) bool { return false }
	if err := r.Sweep(&acts); err != nil || len(acts) != 1 {
		t.Fatalf("%v %v", acts, err)
	}
	if row, _ := db.GetAgentState(r.Q, "a"); !row.ClosedAt.Valid {
		t.Fatal("not closed")
	}
}

func TestAnUnreadTableChangesNothing(t *testing.T) {
	r, _ := newRec(t, map[string]session.Observed{"a": {TerminalAlive: ptr(true)}})
	db.SetTerminal(r.Q, "a", "term1", "/wt")
	var acts []string
	if err := r.Sweep(&acts); err != nil || len(acts) != 0 {
		t.Fatalf("%v %v", acts, err)
	}
}
