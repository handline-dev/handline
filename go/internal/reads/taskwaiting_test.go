package reads

import (
	"reflect"
	"testing"
)

// A queued task with no blocker and nothing asked of the founder waits on the cause the sweep stored with its reason; a record kept
// before the sweep stored causes reads as the owner.
func TestATaskWaitsOnTheCauseTheSweepStored(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'q', 'bob', 'alice', 1, 'alice')")
	run(t, e, `INSERT INTO meta(name, value, updated) VALUES('taskhold:1', '{"why": "waiting for a slot on a (2 of 2 mid-turn)", "on": "cap", "able": false, "since": 1792000000}', 0)`)
	task, _ := clockOf(t, e, 1)
	want := map[string]any{"on": "cap", "text": "waiting for a slot on a (2 of 2 mid-turn)", "tasks": []int64{}, "since": ts(int64(1792000000))}
	if !reflect.DeepEqual(task["waiting"], want) {
		t.Fatalf("%v", task["waiting"])
	}
	run(t, e, `UPDATE meta SET value = '{"why": "bob is mid-turn", "able": false, "since": 1792000000}' WHERE name = 'taskhold:1'`)
	if task, _ = clockOf(t, e, 1); task["waiting"].(map[string]any)["on"] != "owner" {
		t.Fatalf("%v", task["waiting"])
	}
	run(t, e, "UPDATE tasks SET done_evidence = 'x' WHERE id = 1")
	if task, _ = clockOf(t, e, 1); task["waiting"] != nil {
		t.Fatalf("a done task waits on nothing: %v", task["waiting"])
	}
}
