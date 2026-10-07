package supervisor

import (
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
