package agentview

import (
	"reflect"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/timefmt"
)

func TestEachAgentIsServedWithItsStateAndReason(t *testing.T) {
	r := newRig(t, "run", "ask", "crash", "idle", "gone", "work", "nap", "move", "walled", "full", "held")
	r.state(t, "run", states.Running, "")
	r.sess.alive["run"], r.sess.busy["run"] = true, true
	r.state(t, "ask", states.Idle, "")
	r.pr.open["ask"] = &prompts.Block{Kind: "permission", Detail: map[string]any{"tool": "Bash", "command": "ls"}}
	r.state(t, "crash", states.Stalled, "the session exited")
	r.state(t, "idle", states.Idle, "")
	r.sess.alive["idle"] = true
	r.state(t, "gone", states.Idle, "")
	r.state(t, "work", states.Queued, "")
	if err := db.SetAgentState(r.env.Q, "nap", states.Parked, 9000, states.ByHandMark); err != nil {
		t.Fatal(err)
	}
	r.state(t, "move", states.Queued, states.MovingMark+" to b")
	r.state(t, "walled", states.Queued, "")
	r.state(t, "full", states.Queued, "")
	r.state(t, "held", states.Idle, "")
	r.sess.alive["held"] = true
	for _, s := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'mine', 'held', 'm', 1), (2, 'theirs', 'other', 'm', 1)",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(1, 2, 1)",
		"UPDATE agent_state SET current_task = 1 WHERE agent = 'held'",
	} {
		if _, err := r.env.Q.Exec(s); err != nil {
			t.Fatal(err)
		}
	}
	r.env.Walled = func(agent string, _ *db.AgentState) *Wall {
		switch agent {
		case "walled":
			return &Wall{Text: "waiting for a's 5h window (100%, rolls over 04:00)", Reset: 7200}
		case "full":
			return &Wall{Text: "waiting for a slot on a (2 of 2 mid-turn)", Slot: true}
		}
		return nil
	}

	for _, c := range []struct {
		agent, state, reason, text string
		until                      any
		tasks                      []any
	}{
		{"run", "running", "turn", "working", nil, nil},
		{"ask", "waiting", "you", "needs you: Bash: ls", nil, nil},
		{"crash", "stopped", "stalled", "stalled until someone unstalls it: the session exited", nil, nil},
		{"idle", "idle", "none", "idle", nil, nil},
		{"gone", "off", "none", "no terminal", nil, nil},
		{"work", "preparing", "work", "has work: starts at the next tick", nil, nil},
		{"nap", "waiting", "clock", "wakes at " + timefmt.Stamp(9000, timefmt.Clock), "1970-01-01T02:30:00Z", nil},
		{"move", "waiting", "move", "moving to another account", nil, nil},
		{"walled", "stopped", "wall", "waiting for a's 5h window (100%, rolls over 04:00)", "1970-01-01T02:00:00Z", nil},
		{"full", "stopped", "cap", "waiting for a slot on a (2 of 2 mid-turn)", nil, nil},
		{"held", "waiting", "task", "blocked by T2 (other)", nil, []any{float64(2)}},
	} {
		r.env.RefreshOne(c.agent)
		s, _ := r.stored(t, c.agent)["status"].(map[string]any)
		tasks := c.tasks
		if tasks == nil {
			tasks = []any{}
		}
		if s["state"] != c.state || s["reason"] != c.reason || s["text"] != c.text || s["until"] != c.until || !reflect.DeepEqual(s["tasks"], tasks) {
			t.Errorf("%s: %v; want %s/%s %q until %v tasks %v", c.agent, s, c.state, c.reason, c.text, c.until, tasks)
		}
	}
}
