package reads

import (
	"encoding/json"
	"os"
	"testing"
)

// The Python CLI reads these keys unguarded, so every one of the contract's Task must be on the row.
func TestATaskRowCarriesEveryKeyOfTheContractTask(t *testing.T) {
	raw, err := os.ReadFile("../../../contract/protocol.json")
	if err != nil {
		t.Fatal(err)
	}
	var proto struct {
		Components struct {
			Schemas map[string]struct {
				Properties map[string]json.RawMessage `json:"properties"`
			} `json:"schemas"`
		} `json:"components"`
	}
	if err := json.Unmarshal(raw, &proto); err != nil {
		t.Fatal(err)
	}
	keys := proto.Components.Schemas["Task"].Properties
	if len(keys) == 0 {
		t.Fatal("no Task in the contract")
	}
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, discarded_by, discard_reason, discarded_at, edited_by, edited_at) "+
		"VALUES(1, 'x', 'bob', 'human', 1, 'human', 'mgr', 'stale', 50, 'mgr', 20)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(2, 'y', 'bob', 'mgr', 1)")
	got, err := e.Tasks([]int64{1, 2})
	if err != nil {
		t.Fatal(err)
	}
	for id, task := range got {
		for k := range keys {
			if _, ok := task[k]; !ok {
				t.Errorf("T%d lacks %q", id, k)
			}
		}
	}
	d, _ := got[1]["discarded"].(map[string]any)
	if d["by"] != "mgr" || d["reason"] != "stale" || d["at"] != ts(int64(50)) {
		t.Fatalf("a discarded task says who, why and when: %v", got[1]["discarded"])
	}
	if got[1]["last_activity"] != ts(int64(50)) || got[1]["edited_by"] != "mgr" || got[1]["asker"] != "human" {
		t.Fatalf("%v", got[1])
	}
	if got[2]["discarded"] != nil || got[2]["edited_by"] != nil || got[2]["asker"] != "mgr" {
		t.Fatalf("%v", got[2])
	}
}

// Python names the request out; it has no text for a draft whose request is gone, so Go's is the same words without the number.
func TestAMismatchNamesTheRequestOutAndASettledOneLeavesTheNumberOut(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(1, 'x', 'bob', 'mgr', 1, 1)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(7, 3, 'mgr', 'bob', 'b', 'queued', 'task', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 7)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(2, 'y', 'bob', 'mgr', 1, 1)")
	got, err := e.Tasks([]int64{1, 2})
	if err != nil {
		t.Fatal(err)
	}
	if got[1]["mismatch"] != "draft with work out (#7) - its opener has not queued it" ||
		got[2]["mismatch"] != "draft with work out - its opener has not queued it" {
		t.Fatalf("%v / %v", got[1]["mismatch"], got[2]["mismatch"])
	}
	r, _ := got[1]["requests"].([]map[string]any)
	if len(r) != 1 || r[0]["id"] != int64(7) || r[0]["from"] != "mgr" || r[0]["to"] != "bob" || r[0]["state"] != "queued" || r[0]["at"] != ts(int64(3)) {
		t.Fatalf("%v", got[1]["requests"])
	}
}
