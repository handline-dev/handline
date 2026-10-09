package reads

import (
	"testing"

	"github.com/handline/handline/go/internal/db"
)

type fakeDelivery struct {
	wall, cause string
	full        bool
}

func (f fakeDelivery) WallReason(db.Q, string, *db.AgentState) (string, error) { return f.wall, nil }
func (f fakeDelivery) ClosedWakeCause(string, *db.AgentState) (string, error)  { return f.cause, nil }
func (f fakeDelivery) AccountFull(string) (string, int, bool)                  { return "acct", 95, f.full }

func run(t *testing.T, e *Env, q string, args ...any) {
	t.Helper()
	if _, err := e.DB.Exec(q, args...); err != nil {
		t.Fatal(err)
	}
}

func queue(t *testing.T, e *Env, id int64, kind string) {
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(?, ?, 'amy', 'bob', 'b', 'queued', ?, 1)", id, id, kind)
}

func state(t *testing.T, e *Env, st string, closedAt any) {
	run(t, e, "INSERT OR REPLACE INTO agent_state(agent, state, updated, closed_at) VALUES('bob', ?, 0, ?)", st, closedAt)
}

func TestQueueReasonSaysWhyALineWaits(t *testing.T) {
	cases := []struct {
		name  string
		setup func(*Env)
		d     Delivery
		want  string
	}{
		{"not registered", func(e *Env) {}, nil, "not registered"},
		{"never started", func(e *Env) { addAgent(t, e, "bob", "") }, nil, "never started"},
		{"stalled", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "STALLED", nil) }, nil, "stalled"},
		{"a turn running", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "RUNNING", nil) }, nil, "mid-turn"},
		{"mid-handoff", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "HANDOFF", nil) }, nil, "mid-handoff"},
		{"usage wall", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", nil) }, fakeDelivery{wall: "bob is at its usage limit"}, "account at the wall"},
		{"nothing holds it", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", nil) }, nil, "waiting for the agent"},
		{"closed, account full", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", 5) }, fakeDelivery{full: true}, "closed, account full"},
		{"closed, waking", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", 5) }, fakeDelivery{cause: "1 waiting request(s)"}, "closed, waking"},
		{"closed, holding", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", 5)
			run(t, e, "UPDATE requests SET delivered_at = 4 WHERE id = 1")
		}, fakeDelivery{}, "closed, holding"},
		{"closed, nothing to wake it", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", 5) }, fakeDelivery{}, "closed, nothing to wake it"},
	}
	for _, c := range cases {
		e := testEnv(t)
		queue(t, e, 1, "task")
		c.setup(e)
		e.Delivery = c.d
		got, err := e.QueueReason("bob", 1)
		if err != nil || got != c.want {
			t.Errorf("%s: %q, %v; want %q", c.name, got, err, c.want)
		}
	}
}

func TestQueueReasonNamesTheLineAheadAndASay(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	state(t, e, "RUNNING", nil)
	queue(t, e, 1, "task")
	queue(t, e, 2, "task")
	queue(t, e, 3, "say")
	for id, want := range map[int64]string{2: "behind #1", 3: "mid-turn"} { // a say is told now, so only the agent's state holds it
		if got, _ := e.QueueReason("bob", id); got != want {
			t.Errorf("#%d: %q, want %q", id, got, want)
		}
	}
	if got, _ := e.QueueReason("bob", 1); got != "mid-turn" {
		t.Errorf("the head: %q", got)
	}
	run(t, e, "UPDATE agent_state SET state = 'IDLE'")
	if got, _ := e.QueueReason("bob", 3); got != "sending now" {
		t.Errorf("a say with nothing in the way: %q", got)
	}
}

func TestQueueReasonSaysTheHeadsOwnTaskIsBlocked(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	state(t, e, "IDLE", nil)
	queue(t, e, 1, "task")
	queue(t, e, 2, "task")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'human', 1), (2, 'u', 'bob', 'human', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1)")
	run(t, e, "INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(1, 2, 1)")
	if got, _ := e.QueueReason("bob", 1); got != "its own task is blocked" {
		t.Errorf("the blocked head: %q", got)
	}
	if got, _ := e.QueueReason("bob", 2); got != "waiting for the agent" {
		t.Errorf("the line the blocked head hands the queue to is the effective head: %q", got)
	}
}
