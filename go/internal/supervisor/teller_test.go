package supervisor

import (
	"fmt"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/taskwrites"
)

var _ taskwrites.Teller = (*Env)(nil)

func TestTheTellerTypesTheMemoSayAndSendsAQueuedTaskToAFreeOwner(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	tid, _ := db.NewTask(r.DB, "job", "bob", "alice", 0, db.DefaultPriority, "", 0)
	db.SetTaskQueued(r.DB, tid, "human")
	msg := r.QueueDelivered(tid, "human", true)
	if msg != "task T1 is queued (human) - bob was free, so it went out now" {
		t.Fatal(msg)
	}
	r.term.typed = nil
	r.MemoChanged(tid, "alice", "added", "mind the cache")
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
	if err := r.deliver(rid, "alice", "bob", "x", "", team0()); err != nil {
		t.Fatal(err)
	}
	r.term.typed = nil
	say, _ := db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: "T1 discarded by alice: not needed", Kind: "say", Response: "fire_and_forget"})
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
		db.CloseIfQueued(r.DB, q.ID, "dismissed", "", db.CloseOpts{})
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
	r.MemoChanged(1, "alice", "added", "x")
	if !r.logged("telling the holder of T1 about the memo failed") {
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
	if len(said) != 1 || said[0] != "#"+fmt.Sprint(held)+" ended; bob told to stop" || !strings.Contains(strings.Join(r.term.typed, "\n"), "T1 released by alice - stop work on it") {
		t.Fatalf("%q %q", said, r.term.typed)
	}
	db.CloseIfQueued(r.DB, queued, "rejected", "T1 released by alice", db.CloseOpts{NoSettle: true})
	said = r.Released(1, "T1 released by alice", []taskwrites.Retired{{RID: queued, Requester: "alice", Target: "bob", Notify: true}})
	if len(said) != 1 || said[0] != fmt.Sprintf("#%d to bob retired (bob was told to stop)", queued) {
		t.Fatalf("%q", said)
	}
}

func TestTheTellerAfterACancelClosesTheTellsAndTypesTheStopToAnOwnerWithNoDelivery(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "x", "task")
	db.CloseIfQueued(r.DB, rid, "rejected", CancelVerdict, db.CloseOpts{})
	r.term.typed = nil
	r.Cancelled(1, "human", "bob", true, []taskwrites.Retired{{RID: rid, Requester: "alice", Target: "bob", Delivered: true, Notify: true}})
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
