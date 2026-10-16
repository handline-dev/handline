package reads

import (
	"errors"
	"reflect"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// triggerDelivery answers Trigger from a table by agent.
type triggerDelivery struct {
	fakeDelivery
	by map[string]Trigger
}

func (f triggerDelivery) Trigger(_ db.Q, agent string, _ *db.AgentState, _ string) (Trigger, error) {
	return f.by[agent], nil
}

func triggers(t *testing.T, e *Env, params map[string]any) []map[string]any {
	t.Helper()
	got, err := e.agentTriggers(nil, params)
	if err != nil {
		t.Fatal(err)
	}
	return got.(map[string]any)["agents"].([]map[string]any)
}

func TestAgentTriggersSaysWhatTheTickWouldDecideForEachAgent(t *testing.T) {
	e := testEnv(t)
	for _, a := range []string{"ann", "bob", "cat", "dan", "eve", "fay"} {
		addAgent(t, e, a, "")
	}
	writeAgentYAML(t, e, "fay", "- not a map\n")
	run(t, e, "INSERT INTO agent_state(agent, state, updated, terminal, closed_at) VALUES"+
		"('ann', 'IDLE', 0, 'h1', NULL), ('bob', 'IDLE', 0, 'h2', NULL), ('cat', 'PARKED', 0, 'h3', NULL), ('dan', 'IDLE', 0, 'h4', 5)")
	e.Delivery = triggerDelivery{by: map[string]Trigger{
		"ann": {Applies: true, Fired: true, Kind: "usage", Reason: "shortest window 100% - at the wall"},
		"bob": {Applies: true, Reason: "shortest window 10% below the wall, context ? < 150,000"},
		"dan": {Reason: "waiting for alpha's window at 14:00"},
	}}
	got := triggers(t, e, nil)
	want := []map[string]any{
		{"agent": "ann", "fired": true, "reason": "shortest window 100% - at the wall"},
		{"agent": "bob", "fired": false, "reason": "shortest window 10% below the wall, context ? < 150,000"},
		{"agent": "cat", "fired": false, "reason": "parked; judged again at its wake"},
		{"agent": "dan", "fired": false, "reason": "waiting for alpha's window at 14:00"},
		{"agent": "eve", "fired": false, "reason": "session closed; nothing to hand off"},
	}
	if !reflect.DeepEqual(got[:5], want) {
		t.Fatalf("%v", got)
	}
	if r, _ := got[5]["reason"].(string); got[5]["agent"] != "fay" || got[5]["fired"] != false || len(r) < 14 || r[:14] != "config error: " {
		t.Fatalf("an unreadable configuration is said, not fired: %v", got[5])
	}
}

func TestAgentTriggersForOneAgentAndForANameItDoesNotKnow(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "ann", "")
	addAgent(t, e, "bob", "")
	if got := triggers(t, e, map[string]any{"agent": "bob"}); len(got) != 1 || got[0]["agent"] != "bob" {
		t.Fatalf("%v", got)
	}
	var re *rpc.Error
	if _, err := e.agentTriggers(nil, map[string]any{"agent": "zed"}); !errors.As(err, &re) || re.RPCCode != rpc.NotFound {
		t.Fatalf("%v", err)
	}
}
