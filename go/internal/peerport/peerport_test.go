package peerport

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/structured"
)

// fakeInbox answers each text from outcome (Delivered when it has no entry) and keeps what it was given, in order.
type fakeInbox struct {
	mu      sync.Mutex
	texts   []string
	outcome map[string]peerinbox.Result
}

func (f *fakeInbox) Deliver(_ context.Context, _ peerinbox.Session, text string) peerinbox.Result {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.texts = append(f.texts, text)
	if r, ok := f.outcome[text]; ok {
		return r
	}
	return peerinbox.Result{Kind: peerinbox.Delivered}
}

type events struct {
	mu   sync.Mutex
	list []string
}

func (e *events) add(s string) { e.mu.Lock(); e.list = append(e.list, s); e.mu.Unlock() }
func (e *events) get() []string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append([]string(nil), e.list...)
}

func rigOf(in *fakeInbox, ev *events) *Port {
	return &Port{
		Peer:        func(a string) bool { return a == "bob" },
		Find:        func(a string) (peerinbox.Session, error) { return peerinbox.Session{PID: 1}, nil },
		Sender:      in,
		OnDelivered: func(a, op string) { ev.add("delivered " + a + " " + op) },
		OnFailed:    func(a, op, why string) { ev.add("failed " + a + " " + op + ": " + why) },
	}
}

func waitFor(t *testing.T, ev *events, n int) []string {
	t.Helper()
	for i := 0; i < 400; i++ {
		if got := ev.get(); len(got) >= n {
			return got
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("only %v", ev.get())
	return nil
}

func TestMessagesToOneAgentGoOutInOrderAndEachOutcomeComesBack(t *testing.T) {
	in, ev := &fakeInbox{outcome: map[string]peerinbox.Result{"two": {Kind: peerinbox.NotDelivered, Why: "not in the receiver's transcript"}}}, &events{}
	p := rigOf(in, ev)
	for _, m := range []string{"one", "two", "three"} {
		rc, err := p.Send("bob", "op-"+m, m, structured.Now)
		if err != nil || rc.State != structured.Sent {
			t.Fatalf("%v %v", rc, err)
		}
	}
	got := waitFor(t, ev, 3)
	want := []string{"delivered bob op-one", "failed bob op-two: not in the receiver's transcript", "delivered bob op-three"}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%v", got)
		}
	}
}

func TestNothingIsWrittenToAnAgentWithoutAnInbox(t *testing.T) {
	in, ev := &fakeInbox{}, &events{}
	p := rigOf(in, ev)
	p.Find = func(string) (peerinbox.Session, error) {
		return peerinbox.Session{}, errors.New("no live session record")
	}
	if p.Live("bob") {
		t.Fatal("live")
	}
	if _, err := p.Send("bob", "op", "x", structured.Now); err == nil {
		t.Fatal("a send to no inbox must say nothing was written")
	}
	if len(in.texts) != 0 || !p.Structured("bob") || p.Structured("amy") {
		t.Fatal("the port's agents are the ones Peer names")
	}
}

func TestAMessageTheInboxTookCannotBeWithdrawn(t *testing.T) {
	_, ok, err := rigOf(&fakeInbox{}, &events{}).Withdraw("bob", "op")
	if ok || err != nil {
		t.Fatal(ok, err)
	}
}
