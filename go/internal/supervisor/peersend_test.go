package supervisor

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/peerport"
	"github.com/handline/handline/go/internal/structured"
)

// fakeInbox is the peer inbox: it keeps what it was given and answers outcome.
type fakeInbox struct {
	mu      sync.Mutex
	texts   []string
	outcome peerinbox.Result
	gate    chan struct{} // when set, a Deliver waits for it to close
	entered int
}

func (f *fakeInbox) Deliver(_ context.Context, _ peerinbox.Session, text, _ string) peerinbox.Result {
	f.mu.Lock()
	f.entered++
	f.mu.Unlock()
	if f.gate != nil {
		<-f.gate
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.texts = append(f.texts, text)
	return f.outcome
}

func (f *fakeInbox) enteredCount() int { f.mu.Lock(); defer f.mu.Unlock(); return f.entered }

func (f *fakeInbox) count() int { f.mu.Lock(); defer f.mu.Unlock(); return len(f.texts) }

// peerRig is bob, a terminal agent delivered to through its peer inbox, under the real supervisor.
func peerRig(t *testing.T, outcome peerinbox.Result) (*rig, *fakeInbox) {
	r := newRig(t)
	in := &fakeInbox{outcome: outcome}
	var settled atomic.Int32
	// A delivery runs on its own goroutine and tells the database when it ends: the test does not end, and the database does not close,
	// before every delivery that began has told it.
	t.Cleanup(func() {
		for i := 0; i < 2000 && int(settled.Load()) < in.enteredCount(); i++ {
			time.Sleep(5 * time.Millisecond)
		}
	})
	r.Agents = &peerport.Port{
		Peer:   func(a string) bool { return a == "bob" },
		Find:   func(string) (peerinbox.Session, error) { return peerinbox.Session{PID: 7}, nil },
		Sender: in,
		OnDelivered: func(a, op string) {
			defer settled.Add(1)
			if err := r.SessionConfirmed(r.DB, a, op); err != nil {
				t.Error(err)
			}
		},
		OnFailed: func(a, op, why string, written bool) {
			defer settled.Add(1)
			if err := r.SessionNotDelivered(a, op, why, written); err != nil {
				t.Error(err)
			}
		},
	}
	return r, in
}

// peerRigHeld is peerRig whose inbox does not answer until the test ends, for a test that calls sendOnce itself and settles the message by
// hand: the delivery sendOnce starts must not settle it first, nor outlive the database. Wait for in.enteredCount() before settling.
func peerRigHeld(t *testing.T, outcome peerinbox.Result) (*rig, *fakeInbox) {
	r, in := peerRig(t, outcome)
	in.gate = make(chan struct{})
	port := r.Agents.(*peerport.Port)
	settled := make(chan struct{}, 8)
	delivered, failed := port.OnDelivered, port.OnFailed
	port.OnDelivered = func(a, op string) { delivered(a, op); settled <- struct{}{} }
	port.OnFailed = func(a, op, why string, written bool) { failed(a, op, why, written); settled <- struct{}{} }
	t.Cleanup(func() {
		close(in.gate)
		for i := 0; i < in.enteredCount(); i++ {
			select {
			case <-settled:
			case <-time.After(10 * time.Second):
				return
			}
		}
	})
	return r, in
}

func eventually(t *testing.T, ok func() bool) {
	t.Helper()
	for i := 0; i < 400; i++ {
		if ok() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("never happened")
}

func TestATerminalAgentIsToldThroughItsInboxAndTheTranscriptLandsIt(t *testing.T) {
	r, in := peerRig(t, peerinbox.Result{Kind: peerinbox.Delivered})
	rid := r.ask("alice", "bob", "psst", "say")
	if got, err := r.TellNow(r.DB, rid); err != nil || got != "sent" {
		t.Fatalf("%q %v", got, err)
	}
	eventually(t, func() bool { row, _ := db.GetRequest(r.DB, rid); return row.LandedAt.Valid })
	if in.count() != 1 || len(r.term.typed) != 0 {
		t.Fatalf("inbox %d, typed %v: nothing is typed at a terminal", in.count(), r.term.typed)
	}
	if again, _ := r.TellNow(r.DB, rid); again == "sent" || in.count() != 1 {
		t.Fatalf("a retry wrote again: %q", again)
	}
}

func TestATaskQueuedForATerminalAgentGoesThroughTheInboxOnce(t *testing.T) {
	r, in := peerRig(t, peerinbox.Result{Kind: peerinbox.Delivered})
	rid := r.ask("human", "bob", "ship it", "task")
	for i := 0; i < 2; i++ {
		if _, err := r.DeliverQueued(r.DB, "bob"); err != nil {
			t.Fatal(err)
		}
	}
	eventually(t, func() bool { row, _ := db.GetRequest(r.DB, rid); return row.LandedAt.Valid })
	if in.count() != 1 {
		t.Fatalf("%d writes", in.count())
	}
}

func TestAMessageTheTranscriptNeverShowsNeedsAPersonAndIsNotWrittenAgain(t *testing.T) {
	r, in := peerRig(t, peerinbox.Result{Kind: peerinbox.NotDelivered, Why: "not in the receiver's transcript", Attempts: 2})
	rid := r.ask("alice", "bob", "psst", "say")
	if _, err := r.TellNow(r.DB, rid); err != nil {
		t.Fatal(err)
	}
	op := requestOp(rid)
	var told, needs int
	eventually(t, func() bool { // the sender is told last
		r.must(r.DB.QueryRow("SELECT count(*) FROM requests WHERE target = 'alice' AND body LIKE '%transcript never showed it%'").Scan(&told))
		return told > 0
	})
	r.must(r.DB.QueryRow("SELECT count(*) FROM events WHERE agent = 'bob' AND kind = 'needs_you'").Scan(&needs))
	if told != 1 || needs != 1 {
		t.Fatalf("sender told %d, needs-you events %d", told, needs)
	}
	if state, _ := r.sendOnce(r.DB, "bob", op, "psst", structured.Now); state != Unknown || in.count() != 1 {
		t.Fatalf("written again: %v %d", state, in.count())
	}
}

func TestATerminalAgentWithNoInboxIsQueuedNotTold(t *testing.T) {
	r, in := peerRig(t, peerinbox.Result{Kind: peerinbox.Delivered})
	r.Agents.(*peerport.Port).Find = func(string) (peerinbox.Session, error) { return peerinbox.Session{}, peerinbox.ErrNoSession }
	rid := r.ask("alice", "bob", "psst", "say")
	got, err := r.TellNow(r.DB, rid)
	if err != nil || got == "sent" || in.count() != 0 {
		t.Fatalf("%q %v %d", got, err, in.count())
	}
}

func TestAMessageNeverWrittenSaysSoAndOneWrittenAndUnseenSaysItMayHaveBeenRead(t *testing.T) {
	for _, written := range []bool{false, true} {
		r, in := peerRigHeld(t, peerinbox.Result{Kind: peerinbox.Delivered})
		rid := r.ask("alice", "bob", "psst", "say")
		op := requestOp(rid)
		if state, _ := r.sendOnce(r.DB, "bob", op, "psst", structured.Now); state != Written {
			t.Fatal(state)
		}
		eventually(t, func() bool { return in.enteredCount() == 1 })
		r.must(r.SessionNotDelivered("bob", op, "no inbox", written))
		var n int
		pattern := "%was not written to its session (no inbox): it was not read. It is not sent again; send it again.%"
		if written {
			pattern = "%transcript never showed it (no inbox): it may or may not have been read. It is not sent again; ask bob whether it saw it%"
		}
		r.must(r.DB.QueryRow("SELECT count(*) FROM requests WHERE target = 'alice' AND body LIKE ?", pattern).Scan(&n))
		if n != 1 {
			t.Fatalf("written=%v: the sender was told %d times", written, n)
		}
		r.must(r.SessionNotDelivered("bob", op, "no inbox", written)) // settled once
		r.must(r.DB.QueryRow("SELECT count(*) FROM events WHERE agent = 'bob' AND kind = 'needs_you'").Scan(&n))
		if n != 1 {
			t.Fatalf("needs-you events: %d", n)
		}
	}
}

func TestAMessageTheLastDaemonLeftWrittenIsUnknownWhenTheNextSettlesIt(t *testing.T) {
	r, in := peerRigHeld(t, peerinbox.Result{Kind: peerinbox.Delivered})
	rid := r.ask("alice", "bob", "psst", "say")
	op := requestOp(rid)
	if state, _ := r.sendOnce(r.DB, "bob", op, "psst", structured.Now); state != Written {
		t.Fatal(state)
	}
	eventually(t, func() bool { return in.enteredCount() == 1 })
	r.must(r.SessionLost(r.DB, "bob")) // what the daemon does at its start for each peer agent
	if v, _ := db.Cursor(r.DB, ledgerKey("bob", op)); v != sendUnknown {
		t.Fatalf("ledger %d", v)
	}
}
