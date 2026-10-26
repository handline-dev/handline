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
	r := newRig(t, "run", "ask", "crash", "idle", "gone", "work", "nap", "move", "walled", "full", "paced", "held", "bg", "loaded")
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
	r.state(t, "paced", states.Queued, "")
	r.state(t, "held", states.Idle, "")
	r.sess.alive["held"] = true
	r.state(t, "bg", states.Idle, "")
	r.sess.alive["bg"], r.bg.work["bg"] = true, true
	r.state(t, "loaded", states.Queued, "")
	r.env.LoadHeld = func(agent string) string {
		if agent == "loaded" {
			return "held: machine load - 9% of memory free, limit 10%"
		}
		return ""
	}
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
		case "paced":
			return &Wall{Text: "waiting for a's weekly pace: 60% used, 20 points ahead of its line (40%)", Slot: true, Pace: true, Reset: 3600}
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
		{"paced", "waiting", "pace", "waiting for a's weekly pace: 60% used, 20 points ahead of its line (40%)", "1970-01-01T01:00:00Z", nil},
		{"held", "waiting", "task", "blocked by T2 (other)", nil, []any{float64(2)}},
		{"bg", "running", "background", "background work", nil, nil},
		{"loaded", "waiting", "load", "held: machine load - 9% of memory free, limit 10%", nil, nil},
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

// harbor closed its session at 23:07 and a message came at 23:11; every minute the wake was held by the machine's load, while beta's
// slots were full too. It is not off with no terminal: its start is queued, held by the load, which the wake asks first. Without the
// load it waits for the slot; with nothing waiting it is off.
func TestAClosedAgentWithWorkWaitingIsQueuedAndSaysTheLoadHoldsIt(t *testing.T) {
	r := newRig(t, "harbor", "quiet")
	for _, a := range []string{"harbor", "quiet"} {
		r.state(t, a, states.Idle, "")
		if _, err := r.env.Q.Exec("UPDATE agent_state SET closed_at = 4000 WHERE agent = ?", a); err != nil {
			t.Fatal(err)
		}
	}
	r.wake.would["harbor"] = true
	load := "held: machine load - load 5.40 per core, limit 1.50"
	r.env.LoadHeld = func(string) string { return load }
	r.env.Walled = func(agent string, _ *db.AgentState) *Wall {
		if agent == "harbor" {
			return &Wall{Text: "waiting for a slot on beta (3 of 3 mid-turn)", Slot: true}
		}
		return nil
	}
	check := func(agent, state, st, reason, text string) {
		t.Helper()
		r.env.RefreshOne(agent)
		v := r.stored(t, agent)
		s, _ := v["status"].(map[string]any)
		if v["state"] != state || s["state"] != st || s["reason"] != reason || s["text"] != text {
			t.Errorf("%s: %v %v; want %s, %s/%s %q", agent, v["state"], s, state, st, reason, text)
		}
	}
	check("harbor", "queued", "waiting", "load", load)
	check("quiet", "no_terminal", "off", "none", "no terminal")
	load = ""
	check("harbor", "queued", "stopped", "cap", "waiting for a slot on beta (3 of 3 mid-turn)")
	r.env.Walled = func(string, *db.AgentState) *Wall { return nil }
	check("harbor", "queued", "preparing", "work", "has work: starts at the next tick")
}

// The chat showed "handline is at its usage limit" while its row said it was moving to another account. The banner names the wait the
// wake is in, never "every account": a wake may stay on its own account while another has room. Moving, or a limit nothing has acted
// on yet, shows none.
func TestTheLimitBannerNamesTheWaitAndNoneWhileMoving(t *testing.T) {
	r := newRig(t, "move", "walled", "nap", "napper", "sleeper", "fresh", "free")
	for a, why := range map[string]string{"move": "move is at its usage limit", "walled": "walled is at its usage limit",
		"fresh": "fresh is at its usage limit"} {
		r.bg.wall[a] = why
	}
	r.state(t, "move", states.Queued, states.MovingMark+" to b")
	r.state(t, "walled", states.Queued, "")
	for _, a := range []string{"nap", "napper"} { // parked for the window; a message waits for napper only
		r.bg.wall[a] = a + " is parked until its usage window rolls over"
		if err := db.SetAgentState(r.env.Q, a, states.Parked, 9000, "5h limit; wakes at "+timefmt.Stamp(9000, timefmt.Clock)); err != nil {
			t.Fatal(err)
		}
	}
	// sleeper is parked with a message waiting too, but by no limit: the supervisor finds no wall
	if err := db.SetAgentState(r.env.Q, "sleeper", states.Parked, 9000, "wakes at "+timefmt.Stamp(9000, timefmt.Clock)); err != nil {
		t.Fatal(err)
	}
	for _, a := range []string{"napper", "sleeper"} {
		if _, err := db.AddRequest(r.env.Q, db.NewRequest{Requester: "human", Target: a, Body: "hi", Kind: "say"}); err != nil {
			t.Fatal(err)
		}
	}
	r.state(t, "fresh", states.Idle, "")
	r.sess.alive["fresh"] = true
	r.state(t, "free", states.Idle, "")
	r.env.Walled = func(agent string, _ *db.AgentState) *Wall {
		if agent == "walled" {
			// its own account a is walled; b may be free, but the wake stays on a (the move was declined)
			return &Wall{Text: "waiting for a's 5h window (100%, rolls over 02:00)", Reset: 7200}
		}
		return nil
	}
	for agent, want := range map[string]any{
		"move":    nil,
		"walled":  "waiting for a's 5h window (100%, rolls over 02:00)",
		"nap":     nil, // nothing waits for it: idle, nothing held
		"napper":  "waits for its account's usage window, back at " + timefmt.Stamp(9000, timefmt.Clock),
		"sleeper": nil,
		"fresh":   nil,
		"free":    nil,
	} {
		r.env.RefreshOne(agent)
		if got := r.stored(t, agent)["wall_reason"]; got != want {
			t.Errorf("%s: banner %v, want %v", agent, got, want)
		}
	}
}
