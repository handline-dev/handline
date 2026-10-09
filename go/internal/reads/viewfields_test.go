package reads

import (
	"encoding/json"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

type wallDelivery struct{ fakeDelivery }

func (wallDelivery) Wall(db.Q, string, *db.AgentState) (*Wall, error) {
	return &Wall{Account: "acct", Clock: 1700, Assumed: true}, nil
}

func viewOfAgent(t *testing.T, e *Env, name string) map[string]any {
	t.Helper()
	got, err := e.agentsList(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct{ Agents []map[string]any }
	json.Unmarshal(b, &out)
	for _, a := range out.Agents {
		if a["name"] == name {
			return a
		}
	}
	t.Fatalf("%s not listed: %s", name, b)
	return nil
}

func TestAgentViewServesKindTaskCountAndReady(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{"name":"bob","state":"idle"}', 0)`)
	// 1 is bob's current task, queued; 2 is a draft that is not current and does not count; 3 is queued and waits.
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'a', 'bob', 'human', 1, 'human'), (3, 'c', 'bob', 'human', 1, 'human')")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(2, 'b', 'bob', 'human', 1)")
	run(t, e, "INSERT INTO agent_state(agent, state, updated, current_task) VALUES('bob', 'IDLE', 0, 1)")
	v := viewOfAgent(t, e, "bob")
	if k, _ := v["kind"].(string); k == "" {
		t.Fatalf("kind %v", v["kind"])
	}
	if v["task_count"] != float64(2) || v["ready"] != true || v["wall"] != nil {
		t.Fatalf("%v", v)
	}

	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(4, 'blocker', 'amy', 'human', 1)")
	run(t, e, "INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(1, 4, 1)")
	if v = viewOfAgent(t, e, "bob"); v["ready"] != false {
		t.Fatalf("an open blocker holds it: %v", v)
	}
	run(t, e, "UPDATE tasks SET done_evidence = 'x' WHERE id = 4")
	if v = viewOfAgent(t, e, "bob"); v["ready"] != true {
		t.Fatalf("a done blocker holds nothing: %v", v)
	}
	run(t, e, `UPDATE agent_view SET view = '{"name":"bob","state":"running"}'`)
	if v = viewOfAgent(t, e, "bob"); v["ready"] != false {
		t.Fatalf("a running agent is not ready: %v", v)
	}
	// Ready follows the screen word, read back from the wire word the tick writes: IDLE for idle and no_terminal, which a DONE row, a
	// parked one with nothing behind it and a queued one with background work all write.
	for _, c := range []struct {
		row, wire string
		ready     bool
	}{
		{"IDLE", "idle", true}, {"DONE", "idle", true}, {"PARKED", "idle", true}, {"QUEUED", "idle", true}, {"IDLE", "no_terminal", true},
		{"QUEUED", "queued", false}, {"PARKED", "parked", false}, {"RUNNING", "running", false}, {"IDLE", "waiting", false}, {"STALLED", "stalled", false},
	} {
		run(t, e, "UPDATE agent_state SET state = ? WHERE agent = 'bob'", c.row)
		run(t, e, `UPDATE agent_view SET view = json_object('name', 'bob', 'state', ?)`, c.wire)
		if v = viewOfAgent(t, e, "bob"); v["ready"] != c.ready {
			t.Fatalf("%s row, wire %s: ready %v, want %v", c.row, c.wire, v["ready"], c.ready)
		}
	}
}

func TestAStoredViewThatDoesNotDecodeIsAnError(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{not json', 0)`)
	if _, err := e.agentsList(nil, nil); err == nil {
		t.Fatal("served an agent with no name or state")
	}
}

func TestAgentViewServesTheWallTheLifecycleNames(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	e.Delivery = wallDelivery{}
	run(t, e, "INSERT INTO agent_state(agent, state, updated, closed_at) VALUES('bob', 'IDLE', 0, 5)")
	w, _ := viewOfAgent(t, e, "bob")["wall"].(map[string]any)
	if w["account"] != "acct" || w["assumed"] != true || w["clock"] == nil {
		t.Fatalf("%v", w)
	}
}

func TestAStoredWallStandsWhereNoLifecycleCanSayOtherwise(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{"name":"bob","state":"idle","wall":{"account":"acct","clock":null,"assumed":false}}', 0)`)
	if w, _ := viewOfAgent(t, e, "bob")["wall"].(map[string]any); w["account"] != "acct" {
		t.Fatalf("%v", w)
	}
}
