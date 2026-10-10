package supervisor

import (
	"context"
	"sync"
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
}

func (f *fakeInbox) Deliver(_ context.Context, _ peerinbox.Session, text string) peerinbox.Result {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.texts = append(f.texts, text)
	return f.outcome
}

func (f *fakeInbox) count() int { f.mu.Lock(); defer f.mu.Unlock(); return len(f.texts) }

// peerRig is bob, a terminal agent delivered to through its peer inbox, under the real supervisor.
func peerRig(t *testing.T, outcome peerinbox.Result) (*rig, *fakeInbox) {
	r := newRig(t)
	in := &fakeInbox{outcome: outcome}
	r.Agents = &peerport.Port{
		Peer:   func(a string) bool { return a == "bob" },
		Find:   func(string) (peerinbox.Session, error) { return peerinbox.Session{PID: 7}, nil },
		Sender: in,
		OnDelivered: func(a, op string) {
			if err := r.SessionConfirmed(r.DB, a, op); err != nil {
				t.Error(err)
			}
		},
		OnFailed: func(a, op, why string, written bool) {
			if err := r.SessionNotDelivered(a, op, why, written); err != nil {
				t.Error(err)
			}
		},
	}
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
		r, _ := peerRig(t, peerinbox.Result{Kind: peerinbox.Delivered})
		rid := r.ask("alice", "bob", "psst", "say")
		op := requestOp(rid)
		if state, _ := r.sendOnce(r.DB, "bob", op, "psst", structured.Now); state != Written {
			t.Fatal(state)
		}
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
	r, _ := peerRig(t, peerinbox.Result{Kind: peerinbox.Delivered})
	rid := r.ask("alice", "bob", "psst", "say")
	op := requestOp(rid)
	if state, _ := r.sendOnce(r.DB, "bob", op, "psst", structured.Now); state != Written {
		t.Fatal(state)
	}
	r.must(r.SessionLost(r.DB, "bob")) // what the daemon does at its start for each peer agent
	if v, _ := db.Cursor(r.DB, ledgerKey("bob", op)); v != sendUnknown {
		t.Fatalf("ledger %d", v)
	}
}
