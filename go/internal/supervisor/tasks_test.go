package supervisor

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

// newTask opens an approved task with no request out: queued, never delivered.
func (r *rig) newTask(owner, opener, title string) int64 {
	r.t.Helper()
	tid, err := db.NewTask(r.DB, title, owner, opener, 0, db.DefaultPriority, "", 0)
	r.must(err)
	_, err = db.SetTaskQueued(r.DB, tid, opener)
	r.must(err)
	return tid
}

func TestAdvanceDeliversTheNextQueuedTaskAndMakesItCurrent(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	tid := r.newTask("bob", "alice", "write the doc")
	changed, refused, err := r.AdvanceCurrentTask(r.DB, "bob")
	r.must(err)
	if !changed || len(refused) != 0 {
		t.Fatalf("%v %v", changed, refused)
	}
	if cur, ok, _ := db.CurrentTask(r.DB, "bob"); !ok || cur != tid {
		t.Fatalf("current %d %v", cur, ok)
	}
	reqs, _ := db.TaskRequests(r.DB, tid)
	if len(reqs) != 1 || reqs[0].Requester != "alice" || reqs[0].Target != "bob" || reqs[0].Kind.String != "task" || reqs[0].Body != "write the doc" {
		t.Fatalf("%+v", reqs)
	}
	task, _ := db.GetTask(r.DB, tid)
	if !task.SentAt.Valid {
		t.Fatal("sent_at not set")
	}
	// the same task with its request out is still being asked: nothing more to advance
	if changed, _, _ := r.AdvanceCurrentTask(r.DB, "bob"); changed {
		t.Fatal("advanced past a task still asking")
	}
	// the founder holds no current task
	if changed, _, _ := r.AdvanceCurrentTask(r.DB, "human"); changed {
		t.Fatal("advanced the founder")
	}
}

func TestAdvanceRecordsARefusedDeliveryOnceAndSkipsIt(t *testing.T) {
	r := newRig(t)
	tid := r.newTask("bob", "bob", "an agent cannot ask itself")
	other := r.newTask("bob", "alice", "fine")
	changed, refused, err := r.AdvanceCurrentTask(r.DB, "bob")
	r.must(err)
	if !changed {
		t.Fatal("the second task was not delivered")
	}
	if refused[tid] == "" {
		t.Fatalf("%v", refused)
	}
	if cur, _, _ := db.CurrentTask(r.DB, "bob"); cur != other {
		t.Fatal(cur)
	}
	var n int
	r.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'task_deliver_refused'").Scan(&n)
	r.AdvanceCurrentTask(r.DB, "bob")
	var m int
	r.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'task_deliver_refused'").Scan(&m)
	if n != 1 || m != 1 {
		t.Fatalf("a permanent refusal fills the log: %d %d", n, m)
	}
}

func TestAdvanceClearsACurrentTaskThatHasNothingLeft(t *testing.T) {
	r := newRig(t)
	tid := r.newTask("bob", "alice", "done soon")
	db.SetCurrentTask(r.DB, "bob", tid, true)
	db.MarkTaskDone(r.DB, tid, "ok", "reason")
	if changed, _, _ := r.AdvanceCurrentTask(r.DB, "bob"); !changed {
		t.Fatal("not cleared")
	}
	if _, ok, _ := db.CurrentTask(r.DB, "bob"); ok {
		t.Fatal("still current")
	}
	// a current task that is only blocked stays current
	blocked := r.newTask("bob", "alice", "blocked")
	db.SetCurrentTask(r.DB, "bob", blocked, true)
	blocker := r.newTask("alice", "alice", "first")
	db.AddBlock(r.DB, blocked, blocker)
	if changed, _, _ := r.AdvanceCurrentTask(r.DB, "bob"); changed {
		t.Fatal("dropped a blocked current task")
	}
}

func TestQueueDeliverSaysWhetherTheTaskWentOut(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	tid, _ := db.NewTask(r.DB, "job", "bob", "alice", 0, db.DefaultPriority, "", 0)
	db.SetTaskQueued(r.DB, tid, "human")
	row, _ := db.GetTask(r.DB, tid)
	msg, err := r.QueueDeliver(r.DB, tid, "human", true, row)
	r.must(err)
	if msg != "task T1 is queued (human) - bob was free, so it went out now" {
		t.Fatal(msg)
	}
	// a busy owner: queued behind the one it holds
	busy := r.newTask("bob", "alice", "second")
	row, _ = db.GetTask(r.DB, busy)
	msg, _ = r.QueueDeliver(r.DB, busy, "human", true, row)
	if msg != "task T2 is queued (human)" {
		t.Fatal(msg)
	}
	// back to draft
	if msg, _ := r.QueueDeliver(r.DB, busy, "human", false, row); msg != "task T2 is draft (human)" {
		t.Fatal(msg)
	}
	// the founder's task: the card goes back on their board
	ft, _ := db.NewTask(r.DB, "decide", "human", "alice", 0, db.DefaultPriority, "decision", 0)
	row, _ = db.GetTask(r.DB, ft)
	msg, _ = r.QueueDeliver(r.DB, ft, "human", true, row)
	if msg != "task T3 is queued (human) - its card is on the founder's board" {
		t.Fatal(msg)
	}
}

func TestADiscardedBlockerReleasesWhatItBlockedAndTellsItsOwnerOnce(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	blocker := r.newTask("alice", "alice", "first")
	waiting := r.newTask("bob", "alice", "second")
	db.AddBlock(r.DB, waiting, blocker)
	db.DiscardTask(r.DB, blocker, "alice", "not needed")
	freed, err := r.ReleaseEndedBlockers()
	r.must(err)
	if len(freed) != 1 || freed[0] != waiting {
		t.Fatalf("%v", freed)
	}
	if bs, _ := db.TaskBlockers(r.DB, waiting); len(bs) != 0 {
		t.Fatal("still blocked")
	}
	if !strings.Contains(strings.Join(r.term.typed, "\n"), "task T2 is unblocked - T1, its last open blocker, discarded") {
		t.Fatalf("%q", r.term.typed)
	}
	// and it is the owner's current task now
	if cur, _, _ := db.CurrentTask(r.DB, "bob"); cur != waiting {
		t.Fatalf("current %d", cur)
	}
	again, _ := r.ReleaseEndedBlockers()
	if len(again) != 0 {
		t.Fatalf("released twice: %v", again)
	}
}

func TestADoneBlockerReleasesOncePerFinishAndAChildTellsItsParentsOwner(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	parent := r.newTask("bob", "alice", "parent")
	child, _ := db.NewTask(r.DB, "child", "alice", "bob", parent, db.DefaultPriority, "", 0)
	db.AddBlock(r.DB, parent, child)
	db.MarkTaskDone(r.DB, child, "built it", "reason")
	freed, err := r.ReleaseEndedBlockers()
	r.must(err)
	if len(freed) != 1 || freed[0] != parent {
		t.Fatalf("%v", freed)
	}
	var results int
	r.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'child_result_told'").Scan(&results)
	if results != 1 {
		t.Fatalf("the child's result is told to the parent's owner: %d", results)
	}
	// the result is never typed on its own: it rides on bob's next delivery, which the freed owner's advance made at once
	typed := strings.Join(r.term.typed, "\n")
	if !strings.Contains(typed, "Also: T2 done by alice: built it") {
		t.Fatalf("%q", r.term.typed)
	}
	if left, _ := db.PendingNotices(r.DB, "bob"); len(left) != 0 {
		t.Fatalf("a shown notice stays pending: %+v", left)
	}
	if !strings.Contains(strings.Join(r.term.typed, "\n"), "task T1 is unblocked - T2, its last open blocker, done") {
		t.Fatalf("%q", r.term.typed)
	}
	// the block row stays for a reopen: no second release for the same finish
	r.ReleaseEndedBlockers()
	var released int
	r.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'released_at_done'").Scan(&released)
	if released != 1 {
		t.Fatalf("released again for the same finish: %d", released)
	}
}

func TestUrgentResultReadsFailureAndBlockedWords(t *testing.T) {
	for in, want := range map[string]bool{"FAIL: tests": true, "failed to build": true, "Blocked on x": true, "fatal error": true, "done": false, "not a failure": false, "": false} {
		if UrgentResult(in) != want {
			t.Errorf("%q", in)
		}
	}
}

func TestMemoHolderIsTypedOnlyForADoingTaskAndNotTheAuthor(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	tid := r.newTask("bob", "alice", "job")
	r.must(r.TellMemoHolder(r.DB, tid, "alice", "added", "check the cache"))
	if len(r.term.typed) != 0 {
		t.Fatal("told a holder whose task is not doing")
	}
	r.AdvanceCurrentTask(r.DB, "bob")
	r.term.typed = nil
	r.must(r.TellMemoHolder(r.DB, tid, "alice", "added", "check the cache"))
	if !strings.Contains(r.term.last(), "T1 memo added by alice: check the cache") {
		t.Fatalf("%q", r.term.last())
	}
	r.term.typed = nil
	r.must(r.TellMemoHolder(r.DB, tid, "bob", "added", "mine"))
	if len(r.term.typed) != 0 {
		t.Fatal("told the holder of its own memo")
	}
}

func TestSweepTaskQueueDeliversAWaitingTaskToAFreeOwnerAndBacksOffWhenNothingLands(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	tid := r.newTask("bob", "alice", "waiting job")
	r.must(r.SweepTaskQueue(r.clock))
	if cur, _, _ := db.CurrentTask(r.DB, "bob"); cur != tid {
		t.Fatalf("current %d", cur)
	}
	if !r.logged("bob: a queued task was waiting and it is free - delivered") {
		t.Fatal(r.logs)
	}
	// a refusing task sets the retry clock
	r2 := newRig(t)
	r2.newTask("bob", "bob", "refused")
	r2.must(r2.SweepTaskQueue(r2.clock))
	if n, _ := db.Cursor(r2.DB, "taskqueue:bob"); n != r2.clock {
		t.Fatalf("retry clock %d", n)
	}
	r2.logs = nil
	r2.must(r2.SweepTaskQueue(r2.clock + 10))
	var events int
	r2.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'task_deliver_refused'").Scan(&events)
	if events != 1 {
		t.Fatalf("retried inside the minute: %d", events)
	}
}
