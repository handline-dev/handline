package reads

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

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

func TestAgentViewServesCacheIgnoredAndConfigError(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	v := viewOfAgent(t, e, "bob")
	if v["cache_ignored"] != false || v["config_error"] != nil || v["kind"] == nil {
		t.Fatalf("%v", v)
	}
	// The agent's own switch wins over the global one; a configuration that cannot be read is a finding and takes the kind with it.
	os.WriteFile(filepath.Join(e.Home, "config.yaml"), []byte("ignore_cache_expiry: true\n"), 0o644)
	if v = viewOfAgent(t, e, "bob"); v["cache_ignored"] != true {
		t.Fatalf("global switch: %v", v)
	}
	os.WriteFile(filepath.Join(e.Home, "agents", "bob", "agent.yaml"), []byte("ignore_cache_expiry: false\n"), 0o644)
	if v = viewOfAgent(t, e, "bob"); v["cache_ignored"] != false {
		t.Fatalf("own switch: %v", v)
	}
	os.WriteFile(filepath.Join(e.Home, "agents", "bob", "agent.yaml"), []byte("- not a map\n"), 0o644)
	v = viewOfAgent(t, e, "bob")
	if msg, _ := v["config_error"].(string); msg == "" || strings.Contains(msg, "\n") {
		t.Fatalf("config_error %v", v["config_error"])
	}
	if _, has := v["kind"]; has {
		t.Fatalf("kind with an unreadable configuration: %v", v)
	}
}

func TestAgentViewAttentionIsTheMostUrgentOfAskAlertStuckRunning(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	e.Delivery = fakeDelivery{}
	view := func(json string) {
		run(t, e, "INSERT OR REPLACE INTO agent_view(agent, view, updated) VALUES('bob', ?, 0)", json)
	}
	row := func(st string, closedAt any) {
		run(t, e, "INSERT OR REPLACE INTO agent_state(agent, state, updated, closed_at, terminal) VALUES('bob', ?, 0, ?, 't')", st, closedAt)
	}
	attention := func() any { return viewOfAgent(t, e, "bob")["attention"] }
	want := func(label string, got any, w any) {
		t.Helper()
		if got != w {
			t.Fatalf("%s: attention %v, want %v", label, got, w)
		}
	}

	view(`{"name":"bob","state":"idle"}`)
	row("IDLE", nil)
	want("nothing", attention(), nil)
	row("RUNNING", nil)
	want("running", attention(), "running")
	row("RUNNING", 5)
	want("a closed session still carrying RUNNING", attention(), nil)
	row("IDLE", nil)

	run(t, e, `UPDATE agent_view SET view = '{"name":"bob","state":"idle","wall_reason":"zeta is at 100%"}'`)
	want("wall", attention(), "stuck")
	run(t, e, `UPDATE agent_view SET view = '{"name":"bob","state":"waiting","wall_reason":"zeta is at 100%"}'`)
	want("a dialog outranks a wall", attention(), "alert")
	run(t, e, `UPDATE agent_view SET view = '{"name":"bob","state":"idle","block":{"kind":"x","since":"t"}}'`)
	want("a block shown", attention(), "alert")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 1, 'bob', 'human', 'may I?', 'queued', 'ask', 1)")
	want("an open ask outranks all", attention(), "ask")
}

func TestAgentViewAttentionStuckFindingsAboutTheAgent(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{"name":"bob","state":"idle"}', 0)`)
	run(t, e, "INSERT INTO agent_state(agent, state, updated, terminal) VALUES('bob', 'IDLE', 0, 't')")
	attention := func(d fakeDelivery) any {
		e.Delivery = d
		return viewOfAgent(t, e, "bob")["attention"]
	}
	queueAt := func(id, ts int64) {
		run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(?, ?, 'amy', 'bob', 'b', 'queued', 'ask', 1)", id, ts)
	}

	if got := attention(fakeDelivery{}); got != nil {
		t.Fatalf("nothing queued: %v", got)
	}
	// A request a daemon can deliver is a queue working until it has gone on longer than the longest healthy turn.
	queueAt(2, time.Now().Unix()-60)
	if got := attention(fakeDelivery{}); got != nil {
		t.Fatalf("a fresh request: %v", got)
	}
	run(t, e, "UPDATE requests SET ts = ?", time.Now().Unix()-4000)
	if got := attention(fakeDelivery{}); got != "stuck" {
		t.Fatalf("an old request nothing is holding: %v", got)
	}
	// Told, and the agent's own wall holds it: it clears itself, so a told request is not stuck however old.
	run(t, e, "INSERT INTO cursors(name, row_id, updated) VALUES('notify:bob', 2, 0)")
	if got := attention(fakeDelivery{wall: "zeta is at 100%"}); got != nil {
		t.Fatalf("told and self-clearing: %v", got)
	}
	// A reason that does not clear itself counts at once.
	run(t, e, "UPDATE requests SET ts = ?", time.Now().Unix()-10)
	run(t, e, "UPDATE agent_state SET state = 'IDLE', closed_at = 5")
	if got := attention(fakeDelivery{full: true}); got != "stuck" {
		t.Fatalf("closed, account full: %v", got)
	}
	// Quiet states need no one to act.
	if got := attention(fakeDelivery{}); got != nil {
		t.Fatalf("closed, nothing to wake it: %v", got)
	}
	run(t, e, "UPDATE agent_state SET closed_at = NULL")
	run(t, e, "DELETE FROM requests")

	run(t, e, "UPDATE agent_state SET state = 'STALLED'")
	if got := attention(fakeDelivery{}); got != "stuck" {
		t.Fatalf("a FATAL row: %v", got)
	}
	run(t, e, "UPDATE agent_state SET state = 'IDLE'")
	run(t, e, "INSERT INTO handoff(id, agent, step, state, updated, deadline) VALUES('h1', 'bob', 'x', 'in_flight', 0, 0)")
	if got := attention(fakeDelivery{}); got != "stuck" {
		t.Fatalf("a handoff in flight: %v", got)
	}
	run(t, e, "DELETE FROM handoff")
	// A resolve nobody wrote counts once the nudge it was given is the one for that request and it has sat for half an hour.
	if got := attention(fakeDelivery{forgotten: 1800}); got != nil {
		t.Fatalf("not nudged for it: %v", got)
	}
	run(t, e, "INSERT OR REPLACE INTO cursors(name, row_id, updated) VALUES('nudge:bob', 7, 0)")
	if got := attention(fakeDelivery{forgotten: 1799}); got != nil {
		t.Fatalf("not idle long enough: %v", got)
	}
	if got := attention(fakeDelivery{forgotten: 1800}); got != "stuck" {
		t.Fatalf("forgotten: %v", got)
	}
}

func TestAgentViewFireIsWhatTheLifecycleSaysForTheAgentsAccount(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	os.WriteFile(filepath.Join(e.Home, "agents", "bob", "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{"name":"bob","state":"idle","account":"zeta"}', 0)`)
	run(t, e, "INSERT INTO agent_state(agent, state, updated, terminal) VALUES('bob', 'IDLE', 0, 't')")
	e.Delivery = fakeDelivery{fire: "usage_7d"}
	if got := viewOfAgent(t, e, "bob")["fire"]; got != "usage_7d" {
		t.Fatalf("%v", got)
	}
	// With no account there is nothing to read a trigger from; with no lifecycle the stored one stands.
	run(t, e, `UPDATE agent_view SET view = '{"name":"bob","state":"idle"}'`)
	if got := viewOfAgent(t, e, "bob")["fire"]; got != nil {
		t.Fatalf("%v", got)
	}
	e.Delivery = nil
	run(t, e, `UPDATE agent_view SET view = '{"name":"bob","state":"idle","fire":"context","attention":"alert"}'`)
	if v := viewOfAgent(t, e, "bob"); v["fire"] != "context" || v["attention"] != "alert" {
		t.Fatalf("%v", v)
	}
}

// An agent registered and handed work before its first start has no agent_state row; what it is owed is not self-clearing, so it counts at
// once (actions.stuck: "has never been started").
func TestAgentViewAttentionIsStuckForAnAgentNeverStartedWithWorkQueued(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "fresh", "")
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('fresh', '{"name":"fresh","state":"closed"}', 0)`)
	e.Delivery = fakeDelivery{}
	if got := viewOfAgent(t, e, "fresh")["attention"]; got != nil {
		t.Fatalf("nothing queued: %v", got)
	}
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, ?, 'manager', 'fresh', 'b', 'queued', 'ask', 1)", time.Now().Unix())
	if got := viewOfAgent(t, e, "fresh")["attention"]; got != "stuck" {
		t.Fatalf("never started, work queued: %v", got)
	}
}

// Python's _fire is None when agent.yaml reads as an empty map.
func TestAgentViewFireIsNullWhenTheAgentConfigIsEmpty(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{"name":"bob","state":"idle","account":"zeta"}', 0)`)
	run(t, e, "INSERT INTO agent_state(agent, state, updated, terminal) VALUES('bob', 'IDLE', 0, 't')")
	e.Delivery = fakeDelivery{fire: "usage_7d"}
	if got := viewOfAgent(t, e, "bob")["fire"]; got != nil { // addAgent writes an empty agent.yaml
		t.Fatalf("%v", got)
	}
}
