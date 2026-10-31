package supervisor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/taskwrites"
)

var _ taskwrites.Teller = (*Env)(nil)

func TestTheTellerTypesTheMemoSayAndSendsAQueuedTaskToAFreeOwner(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	tid, _ := db.NewTask(r.DB, "job", "bob", "alice", 0, db.DefaultPriority, "", 0, "")
	db.SetTaskQueued(r.DB, tid, "human")
	msg := r.QueueDelivered(tid, "human", true)
	if msg != "task T1 is queued (human) - bob was free, so it went out now" {
		t.Fatal(msg)
	}
	r.term.typed = nil
	r.MemoChanged(tid, 0, "alice", "added", "mind the cache", true, nil)
	if !strings.Contains(r.term.last(), "T1 memo added by alice: mind the cache") {
		t.Fatalf("%q", r.term.last())
	}
	if msg := r.QueueDelivered(9, "human", true); msg != "task T9 is queued (human)" {
		t.Fatalf("a missing task: %q", msg)
	}
}

func TestTheTellerAfterADiscardAdvancesTheOwnerTypesTheSaysAndForgetsInboxFiles(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.agentUp("alice")
	first := r.newTask("bob", "alice", "current")
	second := r.newTask("bob", "alice", "next")
	r.AdvanceCurrentTask(r.DB, "bob")
	db.DiscardTask(r.DB, first, "alice", "not needed")
	rid := r.ask("alice", "bob", "x", "ask")
	if err := r.deliver(rid, "alice", "bob", "x", "", "task", 0); err != nil {
		t.Fatal(err)
	}
	r.term.typed = nil
	say, _ := db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: "T1 discarded by alice: not needed", Kind: "say"})
	r.Discarded("bob", []db.Notice{{RID: say, To: "bob", Text: "T1 discarded by alice: not needed"}}, nil, []db.InboxFile{{Target: "bob", RID: rid}})
	if cur, _, _ := db.CurrentTask(r.DB, "bob"); cur != second {
		t.Fatalf("the owner was not moved on: %d", cur)
	}
	typed := strings.Join(r.term.typed, "\n")
	if !strings.Contains(typed, "T1 discarded by alice: not needed") {
		t.Fatalf("%q", typed)
	}
	if _, err := readInbox(r, rid); err == nil {
		t.Fatal("the inbox file of a closed request stays")
	}
}

func TestTheTellerTypesTheNextTaskAtTheOwnerAfterADiscard(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	first := r.newTask("bob", "alice", "current")
	second := r.newTask("bob", "alice", "the next one")
	r.AdvanceCurrentTask(r.DB, "bob")
	// the next task already has its request out, so the advance only promotes it and the Teller's push is what types it
	rid, err := db.AddRequest(r.DB, db.NewRequest{Requester: "alice", Target: "bob", Body: "the next one", Kind: "task"})
	r.must(err)
	r.must(db.LinkRequest(r.DB, second, rid))
	db.DiscardTask(r.DB, first, "alice", "not needed")
	reqs, _ := db.TaskRequests(r.DB, first)
	for _, q := range reqs {
		db.CloseIfQueued(r.DB, q.ID, "dismissed", "")
	}
	r.clock += BatchEveryS
	r.term.typed = nil
	r.Discarded("bob", nil, nil, nil)
	if typed := strings.Join(r.term.typed, "\n"); !strings.Contains(typed, "the next one") {
		t.Fatalf("the next task was not typed at the owner: %q", typed)
	}
}

func TestAFailedStatementAfterTheCommitIsLoggedByTheTeller(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.DB.Close()
	r.Discarded("bob", nil, nil, nil)
	if !r.logged("moving bob on after a discard failed") {
		t.Fatalf("%v", r.logs)
	}
	if msg := r.QueueDelivered(1, "human", true); msg != "task T1 is queued (human)" || !r.logged("delivering T1 after it was queued failed") {
		t.Fatalf("%q %v", msg, r.logs)
	}
	r.MemoChanged(1, 0, "alice", "added", "x", false, nil)
	if !r.logged("telling about the memo on T1 failed") {
		t.Fatalf("%v", r.logs)
	}
}

func TestTheTellerAfterAReleaseTypesTheStopAtAHolderAndRetiresAQueuedRequest(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	held := r.ask("alice", "bob", "x", "task")
	queued := r.ask("alice", "bob", "y", "task")
	r.term.typed = nil
	said := r.Released(1, "T1 released by alice", []taskwrites.Retired{{RID: held, Requester: "alice", Target: "bob", Held: true}})
	if len(said) != 1 || r.ids(said[0]) != "#"+fmt.Sprint(held)+" ended; bob told to stop" || !strings.Contains(strings.Join(r.term.typed, "\n"), "T1 released by alice - stop work on it") {
		t.Fatalf("%q %q", said, r.term.typed)
	}
	db.MarkDelivered(r.DB, queued) // the target has it, so it is told to stop
	db.CloseIfQueued(r.DB, queued, "rejected", "T1 released by alice")
	said = r.Released(1, "T1 released by alice", []taskwrites.Retired{{RID: queued, Requester: "alice", Target: "bob"}})
	if len(said) != 1 || r.ids(said[0]) != fmt.Sprintf("#%d to bob retired (bob was told to stop)", queued) {
		t.Fatalf("%q", said)
	}
}

func TestTheTellerAfterADraftParksTheHolderAndMovesItOnToItsNextTask(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	first := r.newTask("bob", "alice", "current")
	second := r.newTask("bob", "alice", "the next one")
	r.AdvanceCurrentTask(r.DB, "bob")
	_, err := r.DB.Exec("UPDATE tasks SET queued_by = NULL WHERE id = ?", first) // the draft's own writes: unapproved, its delivery ended
	r.must(err)
	reqs, _ := db.TaskRequests(r.DB, first)
	_, err = r.DB.Exec("UPDATE requests SET state = 'landed', landed_at = 1 WHERE id = ?", reqs[0].ID)
	r.must(err)
	r.must(db.EndHeld(r.DB, first, "bob"))
	r.must(db.RefreshSent(r.DB, first))
	r.term.typed = nil
	said := r.Released(first, fmt.Sprintf("T%d made draft by alice", first), []taskwrites.Retired{{RID: reqs[0].ID, Requester: "alice", Target: "bob", Held: true, Park: true}})
	if len(said) != 1 || r.ids(said[0]) != fmt.Sprintf("#%d ended; bob told to stop and moved on", reqs[0].ID) {
		t.Fatalf("%q", said)
	}
	if typed := strings.Join(r.term.typed, "\n"); !strings.Contains(typed, "it is parked: stop work on it and move on to your next task") {
		t.Fatalf("%q", typed)
	}
	if cur, _, _ := db.CurrentTask(r.DB, "bob"); cur != second {
		t.Fatalf("the holder was not moved on: %d", cur)
	}
}

func TestTheTellerAfterACancelClosesTheTellsAndTypesTheStopToAnOwnerWithNoDelivery(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "x", "task")
	db.CloseIfQueued(r.DB, rid, "rejected", CancelVerdict)
	r.term.typed = nil
	r.Cancelled(1, "human", "bob", true, []taskwrites.Retired{{RID: rid, Requester: "alice", Target: "bob", Delivered: true}})
	if typed := strings.Join(r.term.typed, "\n"); !strings.Contains(typed, "T1 discarded by human - stop work on it") {
		t.Fatalf("%q", typed)
	}
}

func TestTheTellerAfterADoneMovesTheOwnerOnAndReleasesWhatWaitedOnTheTask(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.agentUp("alice")
	first := r.newTask("bob", "alice", "current")
	waiting := r.newTask("alice", "alice", "waits on the first")
	r.must(db.AddBlock(r.DB, waiting, first))
	db.MarkTaskDone(r.DB, first, "done", "reason")
	r.term.typed = nil
	r.Done(first, "bob", "bob", nil, nil, true)
	var n int
	r.DB.QueryRow("SELECT COUNT(*) FROM requests WHERE target = 'alice' AND body LIKE 'task T2 is unblocked%'").Scan(&n)
	if n != 1 {
		t.Fatalf("the owner of the task that waited is told once: %d", n)
	}
}

func TestTheTellerRoutesAHandoffInTheTransactionOfTheMoveAndTypesItAfterTheCommit(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.agentUp("alice")
	tid := r.newTask("bob", "alice", "job")
	tx, err := r.DB.Begin()
	r.must(err)
	var moved int64
	asked, err := r.TaskAsk(tx, taskwrites.AskReq{From: "bob", To: "alice", Kind: "task", Body: "T1 handed to you by bob: job\n\ncheck it", Within: func(rid int64) error {
		moved = rid
		to := "alice"
		return db.EditTask(tx, tid, "bob", db.TaskEdit{Owner: &to})
	}})
	r.must(err)
	if !asked.OK || asked.RID == 0 || moved != asked.RID {
		t.Fatalf("%+v", asked)
	}
	var n int
	r.DB.QueryRow("SELECT COUNT(*) FROM requests").Scan(&n)
	if n != 0 {
		t.Fatal("nothing is visible before the commit")
	}
	r.must(tx.Commit())
	r.term.typed = nil
	r.must(db.LinkRequest(r.DB, tid, asked.RID))
	db.MarkSendNow(r.DB, asked.RID)
	asked.After()
	r.HandedOver(taskwrites.Handover{Task: tid, By: "bob", From: "bob", To: "alice", Title: "job", RID: asked.RID})
	if !strings.Contains(strings.Join(r.term.typed, "\n"), "check it") {
		t.Fatalf("the new owner is typed the handoff: %q", r.term.typed)
	}

	tx, err = r.DB.Begin()
	r.must(err)
	denied, err := r.TaskAsk(tx, taskwrites.AskReq{From: "bob", To: "nobody.such", Kind: "task", Body: "x", Within: func(int64) error { return fmt.Errorf("not reached") }})
	r.must(err)
	r.must(tx.Commit())
	if denied.OK || denied.Msg == "" {
		t.Fatalf("an agent the routing table does not know is refused: %+v", denied)
	}
}

func TestTheTellerSendsATaskThatAnEditGaveToANewOwnerAndTellsTheOldOneToStop(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.agentUp("alice")
	tid, err := db.NewTask(r.DB, "job", "bob", "human", 0, db.DefaultPriority, "", 0, "")
	r.must(err)
	rid := r.ask("human", "bob", "the brief", "task")
	r.must(db.LinkRequest(r.DB, tid, rid))
	r.must(db.SetCurrentTask(r.DB, "bob", tid, true))
	to := "alice"
	r.must(db.EditTask(r.DB, tid, "human", db.TaskEdit{Owner: &to}))
	r.term.typed = nil
	r.Edited(taskwrites.Edited{Task: tid, By: "human", WasOwner: "bob"})
	var old, fresh int
	r.DB.QueryRow("SELECT COUNT(*) FROM requests WHERE id = ? AND state = 'withdrawn'", rid).Scan(&old)
	r.DB.QueryRow("SELECT COUNT(*) FROM requests r JOIN task_requests l ON l.request_id = r.id WHERE l.task_id = ? AND r.target = 'alice' AND r.body = 'the brief'", tid).Scan(&fresh)
	if old != 1 || fresh != 1 {
		t.Fatalf("the old request is taken back (%d) and alice is asked the same brief (%d)", old, fresh)
	}
	if cur, held, _ := db.CurrentTask(r.DB, "bob"); held && cur == tid {
		t.Fatal("bob still holds the task")
	}
	if cur, _, _ := db.CurrentTask(r.DB, "alice"); cur != tid {
		t.Fatalf("alice holds %d", cur)
	}
	if !strings.Contains(strings.Join(r.term.typed, "\n"), "T1 moved to alice by human - stop work on it") {
		t.Fatalf("the old owner is told to stop: %q", r.term.typed)
	}
}

// A team like the live one: agents reach the founder and the founder reaches them, and nothing else is declared, so no edge leads from the
// daemon to an agent, and none from one agent to another.
const founderOnlyTeam = `agents:
  alice: {}
  bob: {}
edges:
  - { from: "*", to: "human" }
  - { from: "human", to: "*" }
`

// The start-up hand-back of the founder's tasks is the daemon's own step: it moves each task to its agent though no edge declares daemon ->
// agent, and leaves those it must; an agent's own handoff on the same team is still refused, and the step runs once.
func TestFounderTasksGoBackWithNoTeamEdgeFromTheDaemon(t *testing.T) {
	r := newRig(t)
	if err := os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(founderOnlyTeam), 0o644); err != nil {
		t.Fatal(err)
	}
	handed := r.newTask("human", "alice", "handed by bob")
	opened := r.newTask("human", "alice", "opened by alice")
	asked := r.newTask("human", "human", "the founder's own")
	gone := r.newTask("human", "carol", "opened by a retired agent")
	_, err := db.AddEvent(r.DB, "bob", "task_handoff", db.KV{K: "task_id", V: handed}, db.KV{K: "from", V: "bob"}, db.KV{K: "to", V: "human"},
		db.KV{K: "note", V: "which one?"})
	r.must(err)

	said, err := taskwrites.ReturnFounderTasks(r.DB, r.home, r.Env)
	if err != nil || fmt.Sprint(said) != fmt.Sprintf("[T%d goes back to bob T%d goes back to alice]", handed, opened) {
		t.Fatalf("%q %v", said, err)
	}
	for tid, want := range map[int64]string{handed: "bob", opened: "alice", asked: "human", gone: "human"} {
		if task, _ := db.GetTask(r.DB, tid); task == nil || task.Owner != want {
			t.Errorf("T%d: owner %+v, want %s", tid, task, want)
		}
	}
	if again, err := taskwrites.ReturnFounderTasks(r.DB, r.home, r.Env); err != nil || len(again) != 0 {
		t.Fatalf("it runs once: %q %v", again, err)
	}

	tx, _ := r.DB.Begin()
	defer tx.Rollback()
	denied, err := r.TaskAsk(tx, taskwrites.AskReq{From: "alice", To: "bob", Kind: "task", Body: "x", Within: func(int64) error { return fmt.Errorf("not reached") }})
	if err != nil || denied.OK || !strings.Contains(denied.Msg, "no edge declares alice -> bob") {
		t.Fatalf("an agent's own handoff still needs its edge: %+v %v", denied, err)
	}
}
