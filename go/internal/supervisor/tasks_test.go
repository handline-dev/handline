package supervisor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// newTask opens an approved task with no request out: queued, never delivered.
func (r *rig) newTask(owner, opener, title string) int64 {
	r.t.Helper()
	tid, err := db.NewTask(r.DB, title, owner, opener, 0, db.DefaultPriority, "", 0, "")
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
	tid, _ := db.NewTask(r.DB, "job", "bob", "alice", 0, db.DefaultPriority, "", 0, "")
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
	ft, _ := db.NewTask(r.DB, "decide", "human", "alice", 0, db.DefaultPriority, "decision", 0, "")
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
	child, _ := db.NewTask(r.DB, "child", "alice", "bob", parent, db.DefaultPriority, "", 0, "")
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

func TestMemoHolderIsTypedOnlyForADoingTaskAndNotTheAuthor(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	tid := r.newTask("bob", "alice", "job")
	r.must(r.TellMemo(r.DB, tid, "alice", "added", "check the cache", false))
	if len(r.term.typed) != 0 {
		t.Fatal("told a holder whose task is not doing")
	}
	r.AdvanceCurrentTask(r.DB, "bob")
	r.term.typed = nil
	r.must(r.TellMemo(r.DB, tid, "alice", "added", "check the cache", false))
	if !strings.Contains(r.term.last(), "T1 memo added by alice: check the cache") {
		t.Fatalf("%q", r.term.last())
	}
	r.term.typed = nil
	r.must(r.TellMemo(r.DB, tid, "bob", "added", "mine", false))
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

// The deadlock that held an agent's whole queue for a day: its current task, begun, waits on a task of its own through a block that went
// dormant (one agent holds both), and that task waits behind it.
func TestSweepDeliversTheDormantBlockerOfABegunTaskBeforeTheRestOfTheQueue(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	held := r.newTask("bob", "alice", "the graph")
	_, _, err := r.AdvanceCurrentTask(r.DB, "bob")
	r.must(err)
	_, err = db.AddEvent(r.DB, "bob", "task_begin", db.KV{K: "task_id", V: held})
	r.must(err)
	blocker := r.newTask("bob", "alice", "the frame the graph follows")
	other := r.newTask("bob", "alice", "something else")
	_, err = r.DB.Exec("INSERT INTO task_blocks(task_id, blocks_on, dormant, created_at) VALUES(?, ?, 1, 1)", held, blocker)
	r.must(err)
	r.must(r.SweepTaskQueue(r.clock))
	if cur, _, _ := db.CurrentTask(r.DB, "bob"); cur != blocker {
		t.Fatalf("current is T%d, want its dormant blocker T%d (not T%d)", cur, blocker, other)
	}
	if !r.logged("bob: its current task waits on a task of its own behind it - delivered that one first") {
		t.Fatal(r.logs)
	}
	// the blocker done, the task that waited on it is current again, ahead of the rest of the queue
	_, err = db.MarkTaskDone(r.DB, blocker, "framed", "test")
	r.must(err)
	_, _, err = r.AdvanceCurrentTask(r.DB, "bob")
	r.must(err)
	if cur, _, _ := db.CurrentTask(r.DB, "bob"); cur != held {
		t.Fatalf("current is T%d, want T%d back once its blocker is done (not T%d)", cur, held, other)
	}
}

// register gives each name an agent directory, so the daemon knows it.
func (r *rig) register(names ...string) {
	for _, a := range names {
		r.must(os.MkdirAll(filepath.Join(r.home, "agents", a), 0o755))
		r.must(os.WriteFile(filepath.Join(r.home, "agents", a, "agent.yaml"), []byte("vendor: claude\n"), 0o644))
	}
}

// saysTo is the bodies of the says written to agent's inbox.
func (r *rig) saysTo(agent string) []string {
	r.t.Helper()
	rows, err := r.DB.Query("SELECT body FROM requests WHERE target=? AND kind='say' ORDER BY id", agent)
	r.must(err)
	defer rows.Close()
	var out []string
	for rows.Next() {
		var b string
		r.must(rows.Scan(&b))
		out = append(out, b)
	}
	return out
}

// The stall this guards against: the holder of a task asked, in a memo on it, an agent with no part in the task to discard a duplicate, and
// nobody was told. The opener and anyone named with @ are told; the writer is not.
func TestAMemoReachesTheOpenerAndTheAgentsItNamesWhateverTheTaskState(t *testing.T) {
	r := newRig(t)
	r.register("carol", "dave")
	r.agentUp("bob")
	tid := r.newTask("bob", "alice", "job")
	r.must(r.TellMemo(r.DB, tid, "bob", "added", "@carol please discard T2, it duplicates this; mail x@dave.example and @nobody", false)) // local-ref-ok: an address names nobody
	if says := r.saysTo("carol"); len(says) != 1 || !strings.Contains(says[0], "T1 memo added by bob: @carol please discard T2") {
		t.Fatalf("the agent named was not told: %q", says)
	}
	if says := r.saysTo("alice"); len(says) != 1 {
		t.Fatalf("the opener was not told: %q", says)
	}
	for _, a := range []string{"bob", "dave", "nobody", db.Human} {
		if says := r.saysTo(a); len(says) != 0 {
			t.Fatalf("%s was told: %q", a, says)
		}
	}
	r.must(r.TellMemo(r.DB, tid, "alice", "edited", "@alice @carol mind the cache", false))
	if len(r.saysTo("alice")) != 1 || len(r.saysTo("carol")) != 2 || len(r.saysTo("bob")) != 0 {
		t.Fatalf("the writer was told, the agent named was not, or the holder of a task not doing was: %q %q %q", r.saysTo("alice"), r.saysTo("carol"), r.saysTo("bob"))
	}
}

// A memo to a reader mid-turn waits for the end of its turn, past the result digest's window, and every memo held for it then goes out as
// one message naming each task.
func TestAMemoWaitsForTheEndOfTheReadersTurnAndGoesOutWithTheOthersHeld(t *testing.T) {
	r := newRig(t)
	r.register("carol")
	r.agentUp("carol")
	r.must(db.SetAgentState(r.DB, "carol", states.Running, 0, ""))
	one, two := r.newTask("bob", "carol", "one"), r.newTask("bob", "carol", "two")
	r.must(r.TellMemo(r.DB, one, "alice", "added", "first", false))
	r.must(r.TellMemo(r.DB, two, "alice", "edited", "second", false))
	r.must(r.SweepNotices(r.clock + 2*900))
	if len(r.term.typed) != 0 {
		t.Fatalf("a reader mid-turn was told: %q", r.term.typed)
	}
	r.must(db.SetAgentState(r.DB, "carol", states.Idle, 0, ""))
	r.must(r.SweepNotices(r.clock + 2*900))
	if len(r.term.typed) != 1 || !strings.Contains(r.term.last(), "T1 memo added by alice: first") || !strings.Contains(r.term.last(), "T2 memo edited by alice: second") {
		t.Fatalf("the held memos were not told as one message at the turn's end: %q", r.term.typed)
	}
	if left, _ := db.PendingNotices(r.DB, "carol"); len(left) != 0 {
		t.Fatal("told memos stay held")
	}
}

// Told at once: a reader at its prompt, the holder of a doing task (it is its current task) even mid-turn, and anyone when the memo is sent now.
func TestAMemoIsToldAtOnceToAnIdleReaderTheHolderOfItsCurrentTaskAndWhenSentNow(t *testing.T) {
	r := newRig(t)
	r.register("carol")
	r.agentUp("carol")
	r.agentUp("bob")
	r.must(db.SetAgentState(r.DB, "carol", states.Idle, 0, ""))
	tid := r.newTask("bob", "carol", "job")
	r.must(r.TellMemo(r.DB, tid, "alice", "added", "idle", false))
	if len(r.term.typed) != 1 || !strings.Contains(r.term.last(), "T1 memo added by alice: idle") {
		t.Fatalf("an idle reader was not told at once: %q", r.term.typed)
	}
	r.AdvanceCurrentTask(r.DB, "bob")
	r.must(db.SetAgentState(r.DB, "bob", states.Running, 0, ""))
	r.must(db.SetAgentState(r.DB, "carol", states.Running, 0, ""))
	r.term.typed = nil
	r.must(r.TellMemo(r.DB, tid, "alice", "added", "current", false))
	if len(r.term.typed) != 1 || len(r.saysTo("carol")) != 2 {
		t.Fatalf("the holder of its current task was not told at once, or the reader mid-turn was: %q", r.term.typed)
	}
	if left, _ := db.PendingNotices(r.DB, "carol"); len(left) != 1 {
		t.Fatalf("the reader mid-turn has no memo held: %d", len(left))
	}
	r.term.typed = nil
	r.must(r.TellMemo(r.DB, tid, "alice", "added", "urgent", true))
	if len(r.term.typed) != 2 {
		t.Fatalf("a memo sent now was not told at once to both readers: %q", r.term.typed)
	}
}

// A held memo still unread an hour on is told to the reader's lead, once, and stays held for the reader.
func TestAHeldMemoUnreadForAnHourIsToldToTheReadersLeadOnce(t *testing.T) {
	r := newRig(t)
	r.register("carol")
	r.must(os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(strings.Replace(teamYAML, "edges:\n",
		"edges:\n  - { from: bob, to: carol, relation: supervisor, response: async }\n", 1)), 0o644))
	r.agentUp("bob")
	r.agentUp("carol")
	r.must(db.SetAgentState(r.DB, "carol", states.Running, 0, ""))
	tid := r.newTask("alice", "carol", "job")
	r.must(r.TellMemo(r.DB, tid, "alice", "added", "please look", false))
	r.must(r.SweepNotices(r.clock + MemoLeadS - 1))
	if len(r.term.typed) != 0 {
		t.Fatalf("told before the hour: %q", r.term.typed)
	}
	r.must(r.SweepNotices(r.clock + MemoLeadS))
	if says := r.saysTo("bob"); len(says) != 1 || !strings.Contains(says[0], "carol has not read this memo in an hour") || !strings.Contains(says[0], "T1 memo added by alice: please look") {
		t.Fatalf("the lead was not told: %q", says)
	}
	r.must(r.SweepNotices(r.clock + 2*MemoLeadS))
	if len(r.saysTo("bob")) != 1 {
		t.Fatal("the lead was told twice")
	}
	if left, _ := db.PendingNotices(r.DB, "carol"); len(left) != 1 {
		t.Fatal("the memo is no longer held for its reader")
	}
}
