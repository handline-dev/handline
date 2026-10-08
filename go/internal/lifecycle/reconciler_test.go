package lifecycle

import (
	"errors"
	"io/fs"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/term"
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

type reconTerm struct {
	promptGone, idle bool
	blocker          *term.Blocker
}

func (reconTerm) Available() bool                      { return true }
func (t reconTerm) TrustPromptGone(string) bool        { return t.promptGone }
func (t reconTerm) ScreenBlocker(string) *term.Blocker { return t.blocker }
func (t reconTerm) IdleNow(string) bool                { return t.idle }

func TestReconcileWakesDueClosedAgentsAndKeepsAFailedWakeDue(t *testing.T) {
	r, _ := newRec(t, nil)
	var enforced []string
	woke, wakeOK := 0, false
	r.Ports.Agents = func() []string { return []string{"a", "b", "new"} }
	r.Ports.EnforceEdges = func(_ db.Q, a string) error { enforced = append(enforced, a); return nil }
	r.Ports.WaitingFor = func(db.Q, string) (bool, error) { return true, nil }
	r.Ports.WakeAgent = func(db.Q, string) (bool, string, error) { woke++; return wakeOK, "no room", nil }
	r.Ports.QueuedOrIdle = func(q db.Q, a, why string) (string, error) {
		return states.Idle, db.SetAgentState(q, a, states.Idle, 0, why)
	}
	for _, a := range []string{"a", "b"} {
		db.SetAgentState(r.Q, a, states.Parked, 4000, "")
		db.SetTerminal(r.Q, a, "t-"+a, "/wt")
		db.SetClosed(r.Q, a, "s1")
	}
	acts, err := r.Reconcile()
	if err != nil || woke != 2 || len(enforced) != 2 {
		t.Fatalf("%v %v %d", acts, err, woke)
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Parked || row.WakeAt.Int64 != 4000 {
		t.Fatalf("a failed wake stays due: %+v", row)
	}
	if !strings.Contains(strings.Join(acts, "|"), "a: could not wake at reconciliation, still due - no room") || !strings.Contains(strings.Join(acts, "|"), "new: IDLE (first seen)") {
		t.Fatalf("%v", acts)
	}
	wakeOK = true
	acts, _ = r.Reconcile()
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Idle {
		t.Fatalf("%+v %v", row, acts)
	}
	if !strings.Contains(strings.Join(acts, "|"), "a: woken at reconciliation") || !strings.Contains(strings.Join(acts, "|"), "a: PARKED -> IDLE (wake_at was 4000)") {
		t.Fatalf("%v", acts)
	}
}

func TestAHandoffThatCannotBeResumedCountsAgainstItsAgentUntilItStalls(t *testing.T) {
	r, _ := newRec(t, nil)
	r.Ports.ResumeHandoff = func(db.Q, db.HandoffRow) error { return errors.New("agent.yaml unreadable") }
	db.StartHandoff(r.Q, "h1", "a", "", "idle", 0)
	var acts []string
	for i := 0; i < states.MaxFailedAttempts; i++ {
		var err error
		if acts, err = r.Reconcile(); err != nil {
			t.Fatal(err)
		}
	}
	row, _ := db.GetAgentState(r.Q, "a")
	if row.State != states.Stalled || row.FailedAttempts != states.MaxFailedAttempts || !strings.Contains(row.Detail.String, "could not resume handoff from step 'requested'") {
		t.Fatalf("%+v %v", row, acts)
	}
}

func TestASweepLiftsAStallWhoseScreenIsAnswered(t *testing.T) {
	alive := map[string]session.Observed{"a": {TerminalAlive: ptr(true), SessionSeen: true, SessionPID: ptr(7)}}
	r, _ := newRec(t, alive)
	r.Ports.Term = reconTerm{promptGone: true}
	r.Ports.LiftLogin = func(db.Q, string, *db.AgentState, *[]string) {}
	db.SetTerminal(r.Q, "a", "t1", "/wt")
	db.SetAgentState(r.Q, "a", states.Stalled, 0, "a's terminal is showing claude's "+TrustStall)
	var acts []string
	if err := r.Sweep(&acts); err != nil {
		t.Fatal(err)
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Idle || len(acts) != 1 || !strings.Contains(acts[0], "STALLED -> IDLE") {
		t.Fatalf("%+v %v", row, acts)
	}
	// A dialog stall waits for the dialog to go.
	db.SetAgentState(r.Q, "a", states.Stalled, 0, "a's terminal is showing a "+DialogStall)
	r.Ports.Term = reconTerm{blocker: &term.Blocker{Kind: term.Modal}}
	if err := r.Sweep(&acts); err != nil {
		t.Fatal(err)
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Stalled {
		t.Fatalf("lifted while the dialog stands: %+v", row)
	}
	r.Ports.Term = reconTerm{}
	if err := r.Sweep(&acts); err != nil {
		t.Fatal(err)
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Idle {
		t.Fatalf("%+v", row)
	}
}

func TestARunningAgentIsUnstuckOnlyWithTwoWitnesses(t *testing.T) {
	alive := map[string]session.Observed{"a": {TerminalAlive: ptr(true), SessionSeen: true, SessionPID: ptr(7)}}
	r, _ := newRec(t, alive) // now is 5000
	db.SetTerminal(r.Q, "a", "t1", "/wt")
	db.SetAgentState(r.Q, "a", states.Running, 0, "")
	db.AddEventAt(r.Q, 1, "a", "Stop")
	r.Ports.Term = reconTerm{idle: false}
	var acts []string
	r.Sweep(&acts)
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Running {
		t.Fatal("Orca says it is mid-turn")
	}
	r.Ports.Term = reconTerm{idle: true, blocker: &term.Blocker{Kind: term.Modal}}
	r.Sweep(&acts)
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Running {
		t.Fatal("an unanswered prompt is not idle")
	}
	r.Ports.Term = reconTerm{idle: true}
	if err := r.Sweep(&acts); err != nil {
		t.Fatal(err)
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Idle || len(acts) != 1 || !strings.Contains(acts[0], "back to IDLE") {
		t.Fatalf("%+v %v", row, acts)
	}
}

func TestADescriptorShortageIsNotCountedAgainstTheAgent(t *testing.T) {
	r, logs := newRec(t, nil)
	r.Ports.ResumeHandoff = func(db.Q, db.HandoffRow) error {
		return &fs.PathError{Op: "open", Path: "agent.yaml", Err: syscall.EMFILE}
	}
	db.StartHandoff(r.Q, "h1", "a", "", "idle", 0)
	for i := 0; i < states.MaxFailedAttempts+1; i++ {
		if _, err := r.Reconcile(); err != nil {
			t.Fatal(err)
		}
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row != nil && (row.State == states.Stalled || row.FailedAttempts != 0) {
		t.Fatalf("%+v", row)
	}
	if n := strings.Count(strings.Join(*logs, "\n"), "out of file descriptors"); n != 1 {
		t.Fatalf("said %d times in one minute", n)
	}
}

func TestOnlyAnAgentsOwnFailureStallsItAndTheDaemonsPropagates(t *testing.T) {
	r, _ := newRec(t, nil)
	db.StartHandoff(r.Q, "h1", "a", "", "idle", 0)
	r.Ports.ResumeHandoff = func(db.Q, db.HandoffRow) error { return errors.New("database is locked") }
	if _, err := r.Reconcile(); err == nil {
		t.Fatal("a failed statement was counted against the agent")
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row != nil && row.FailedAttempts != 0 {
		t.Fatalf("%+v", row)
	}
	// A wake that fails on the agent's own config counts against it; any other error ends the pass.
	r.Ports.ResumeHandoff = func(db.Q, db.HandoffRow) error { return nil }
	db.AdvanceHandoff(r.Q, "h1", "done", "finished", "")
	db.SetAgentState(r.Q, "w", states.Parked, 4000, "")
	db.SetTerminal(r.Q, "w", "t", "/wt")
	db.SetClosed(r.Q, "w", "s")
	r.Ports.EnforceEdges = func(db.Q, string) error { return nil }
	r.Ports.WaitingFor = func(db.Q, string) (bool, error) { return true, nil }
	r.Ports.QueuedOrIdle = func(db.Q, string, string) (string, error) { return states.Idle, nil }
	r.Ports.WakeAgent = func(db.Q, string) (bool, string, error) { return false, "", &ConfigError{errors.New("bad yaml")} }
	acts, err := r.Reconcile()
	if err != nil || !strings.Contains(strings.Join(acts, "|"), "w: could not wake at reconciliation: bad yaml") {
		t.Fatalf("%v %v", acts, err)
	}
	if row, _ := db.GetAgentState(r.Q, "w"); row.FailedAttempts != 1 {
		t.Fatalf("%+v", row)
	}
	r.Ports.WakeAgent = func(db.Q, string) (bool, string, error) { return false, "", errors.New("database is locked") }
	if _, err := r.Reconcile(); err == nil {
		t.Fatal("a database error at the wake disappeared")
	}
	r.Ports.WaitingFor = func(db.Q, string) (bool, error) { return false, errors.New("database is locked") }
	if _, err := r.Reconcile(); err == nil {
		t.Fatal("a database error reading what is waiting disappeared")
	}
}
