package reads

import (
	"testing"
)

// withTerminal: each state is given the handle a terminal agent would have, so a missing terminal is not what it reads.
func withTerminal(t *testing.T, e *Env) {
	run(t, e, "UPDATE agent_state SET terminal = 'h1' WHERE agent = 'bob'")
}

// The wants are what Python's queue_reason (actions/stuck.py, removed with the Python daemon) answered in each state.
func TestQueueReasonInEachState(t *testing.T) {
	cases := []struct {
		name  string
		setup func(*Env)
		d     Delivery
		rid   int64
		want  string
	}{
		{"not registered", func(e *Env) {}, nil, 1, "not registered"},
		{"never started", func(e *Env) { addAgent(t, e, "bob", "") }, nil, 1, "never started"},
		{"stalled", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "STALLED", nil); withTerminal(t, e) }, nil, 1, "stalled"},
		{"a turn running", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "RUNNING", nil); withTerminal(t, e) }, nil, 1, "mid-turn"},
		{"mid-handoff", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "HANDOFF", nil); withTerminal(t, e) }, nil, 1, "mid-handoff"},
		{"nothing holds it", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", nil); withTerminal(t, e) }, nil, 1, "waiting for the agent"},
		{"closed, waking", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", 5); withTerminal(t, e) }, fakeDelivery{cause: "work"}, 1, "closed, waking"},
		{"closed, holding", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", 5)
			withTerminal(t, e)
			run(t, e, "UPDATE requests SET delivered_at = 4 WHERE id = 1")
		}, fakeDelivery{}, 1, "closed, holding"},
		{"closed, nothing to wake it", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", 5)
			withTerminal(t, e)
			run(t, e, "UPDATE requests SET state = 'answered' WHERE id = 1")
		}, fakeDelivery{}, 1, "closed, nothing to wake it"},
		{"behind the head", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", nil)
			withTerminal(t, e)
			queue(t, e, 2, "task")
		}, nil, 2, "behind #1"},
		{"a say with a turn running", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "RUNNING", nil)
			withTerminal(t, e)
			queue(t, e, 2, "say")
		}, nil, 2, "mid-turn"},
		{"a say with nothing in the way", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", nil)
			withTerminal(t, e)
			queue(t, e, 2, "say")
			run(t, e, "UPDATE requests SET state = 'answered' WHERE id = 1")
		}, nil, 2, "sending now"},
	}
	for _, c := range cases {
		e := testEnv(t)
		queue(t, e, 1, "task")
		c.setup(e)
		e.Delivery = c.d
		got, err := e.QueueReason("bob", c.rid)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if got != c.want {
			t.Errorf("%s: %q, want %q", c.name, got, c.want)
		}
	}
}
