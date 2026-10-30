package reads

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestRetroCountsTokensInsideTheDoingWindowsOfTheSubtreeOnly(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO agent_state(agent, state, account, worktree, updated) VALUES('grp.one','IDLE','a','/w/one',1), ('grp.two','IDLE','a','/w/two',1)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, reporter, created_at, done_at, done_evidence) VALUES"+
		"(10, 'root', 'grp.two', 'human', 'human', 1000, 4600, 'ok'),"+
		"(11, 'child', 'grp.one', 'grp.two', 'grp.two', 1000, 2800, 'ok'),"+
		"(12, 'other', 'grp.one', 'human', 'grp.two', 1000, 4600, 'ok')")
	run(t, e, "UPDATE tasks SET parent_task = 10 WHERE id = 11")
	ev := func(ts int64, agent, kind string, task int) {
		run(t, e, "INSERT INTO events(ts, agent, kind, payload) VALUES(?,?,?,?)", ts, agent, kind, `{"task_id":`+itoa(int64(task))+`}`)
	}
	ev(1000, "grp.one", "task_start", 11)
	ev(2800, "grp.one", "mark_task_done", 11)
	ev(2800, "grp.two", "task_start", 10)
	ev(4600, "grp.two", "mark_task_done", 10)
	ev(1000, "grp.one", "task_start", 12) // not in the subtree
	call := func(ts int64, cwd string, n int) {
		run(t, e, "INSERT INTO token_calls(account, session, req, ts, model, cwd, side, inp, cc, cr, out, thinking) VALUES('a','s',?,?,'m',?,0,?,0,0,0,0)",
			itoa(ts)+cwd, ts, cwd, n)
	}
	call(1500, "/w/one", 100)
	call(3000, "/w/one", 500) // after grp.one's window
	call(3000, "/w/two", 200)
	call(3000, "/elsewhere", 900) // no agent's worktree
	got, err := e.retroAt(10, 5000)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct {
		Wall   int64 `json:"wall_seconds"`
		Tokens int64
		Agents []struct {
			Agent                 string
			Doing_seconds, Tokens int64
			Tasks                 []int64
		}
	}
	json.Unmarshal(b, &out)
	if out.Wall != 3600 || out.Tokens != 300 || len(out.Agents) != 2 {
		t.Fatalf("got %s", b)
	}
	if a := out.Agents[0]; a.Agent != "grp.two" || a.Doing_seconds != 1800 || a.Tokens != 200 || !reflect.DeepEqual(a.Tasks, []int64{10}) {
		t.Fatalf("first %s", b)
	}
	if a := out.Agents[1]; a.Agent != "grp.one" || a.Doing_seconds != 1800 || a.Tokens != 100 || !reflect.DeepEqual(a.Tasks, []int64{11}) {
		t.Fatalf("second %s", b)
	}
	if _, err := e.retroAt(12, 5000); err == nil {
		t.Fatal("a task that answers to an agent is refused")
	}
	if _, err := e.retroAt(999, 5000); err == nil {
		t.Fatal("a missing task is not found")
	}
}
