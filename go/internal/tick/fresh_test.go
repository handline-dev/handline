package tick

import (
	"testing"
)

func taskOf(t *testing.T, tk *Tick, owner string, doneAt int64) {
	t.Helper()
	var err error
	if doneAt > 0 {
		_, err = tk.Q.Exec("INSERT INTO tasks(title, owner, opened_by, created_at, done_evidence, done_at) VALUES('t', ?, 'lead', 1, 'ok', ?)", owner, doneAt)
	} else {
		_, err = tk.Q.Exec("INSERT INTO tasks(title, owner, opened_by, created_at) VALUES('t', ?, 'lead', 1)", owner)
	}
	if err != nil {
		t.Fatal(err)
	}
}

func TestAnAgentThatStartsFreshAfterATaskIsHandedOffOnceItsTaskIsDone(t *testing.T) {
	tk, w := newWorld(t)
	w.cfg = map[string]any{FreshKey: true}
	w.performOK = true
	taskOf(t, tk, "a", 9_000)
	if actions := supervise(tk, "a"); !w.did("handoff a task_done") || !has(actions, "a: handoff (task_done)") {
		t.Fatalf("%v %v", w.calls, actions)
	}
}

func TestAFreshStartIsNotOwedWhenTheSwitchIsOffAnotherTaskIsOpenOrTheSessionBeganAfterwards(t *testing.T) {
	cases := map[string]func(tk *Tick, w *world){
		"switch off (a non-auditor)": func(tk *Tick, w *world) { w.cfg = map[string]any{} },
		"a second task is open":      func(tk *Tick, w *world) { taskOf(t, tk, "a", 0) },
		"another agent's task is open": func(tk *Tick, w *world) {
			taskOf(t, tk, "b", 0) // not this agent's: no reset is held back, but this case must still fire
			w.cfg = map[string]any{FreshKey: true}
		},
		"a session began after the task": func(tk *Tick, w *world) {
			if _, err := tk.Q.Exec("INSERT INTO events(ts, agent, kind) VALUES(9500, 'a', 'SessionStart')"); err != nil {
				t.Fatal(err)
			}
		},
		"a handoff followed the task": func(tk *Tick, w *world) {
			if err := startedHandoff(tk, "a", 9_500); err != nil {
				t.Fatal(err)
			}
		},
		"no task was ever done": func(tk *Tick, w *world) {
			if _, err := tk.Q.Exec("DELETE FROM tasks"); err != nil {
				t.Fatal(err)
			}
		},
	}
	for name, mutate := range cases {
		tk, w := newWorld(t)
		w.cfg = map[string]any{FreshKey: true}
		w.performOK = true
		taskOf(t, tk, "a", 9_000)
		mutate(tk, w)
		supervise(tk, "a")
		fired := w.did("handoff a task_done")
		if name == "another agent's task is open" {
			if !fired {
				t.Errorf("%s: another agent's open task held this one back", name)
			}
			continue
		}
		if fired {
			t.Errorf("%s: handed off", name)
		}
	}
}

func startedHandoff(tk *Tick, agent string, at int64) error {
	_, err := tk.Q.Exec("INSERT INTO handoff(id, agent, session_id, step, state, trigger_kind, updated, deadline) VALUES('h1', ?, 's', 'released', 'done', 'context', ?, ?)", agent, at, at+300)
	return err
}
