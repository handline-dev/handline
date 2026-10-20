package reads

import (
	"encoding/json"
	"testing"
)

// A gate task shows what it watches and, open, its status says so; once open it carries its result.
func TestAGateTaskShowsItsWatchAndIsRunningWhileItWatches(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at, watch_vendor, watch_target, watch_until, watch_label) "+
		"VALUES(1, 'wait for CI', 'gate', 'bob', 1, 'bob', 2, 'github', 'o/r#412', 'ci', 'PR #412 CI')")
	get := func() map[string]any {
		t.Helper()
		got, err := e.taskGet(nil, map[string]any{"task": json.Number("1")})
		if err != nil {
			t.Fatal(err)
		}
		b, _ := json.Marshal(got)
		var m map[string]any
		json.Unmarshal(b, &m)
		return m
	}
	task := get()
	w, _ := task["watch"].(map[string]any)
	st, _ := task["status"].(map[string]any)
	if x := expectedOf(t, e, 1, 2000); x != nil {
		t.Fatalf("a gate has no expected time: %v", x)
	}
	if task["state"] != "doing" || w["vendor"] != "github" || w["target"] != "o/r#412" || w["until"] != "ci" || w["label"] != "PR #412 CI" ||
		w["result"] != nil || st["state"] != "running" || st["reason"] != "watch" || st["text"] != "watching PR #412 CI" {
		t.Fatalf("%v", task)
	}
	run(t, e, "UPDATE tasks SET watch_error = 'gh: HTTP 502' WHERE id = 1")
	if st, _ := get()["status"].(map[string]any); st["text"] != "watching PR #412 CI - last read failed: gh: HTTP 502" {
		t.Fatalf("%v", st)
	}
	run(t, e, "UPDATE tasks SET done_evidence = 'ci passed', done_at = 3, watch_result = 'passed' WHERE id = 1")
	if w, _ := get()["watch"].(map[string]any); w["result"] != "passed" {
		t.Fatalf("%v", w)
	}
}
