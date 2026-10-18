package reads

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

func segmentsOf(t *testing.T, e *Env, tid, now int64) []string {
	t.Helper()
	got, err := e.taskTimeAt(tid, now)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, s := range got.(map[string]any)["segments"].([]map[string]any) {
		from, _ := time.Parse(time.RFC3339, s["from"].(string))
		out = append(out, fmt.Sprintf("%v %v %d-%d", s["kind"], s["text"], from.Unix(), from.Unix()+s["seconds"].(int64)))
	}
	return out
}

// A task queued, then waiting on the founder's answer, then blocked by another task, then worked, with its holder at an account's wall and
// restarting in the middle of the work.
func TestATasksTimeIsItsWaitsAndItsWorkInOrderWithNoGap(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, done_at) VALUES(1, 'a', 'bob', 'amy', 100, 400)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, done_at) VALUES(2, 'b', 'cy', 'bob', 200, 300)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, state_at, kind) VALUES(1, 110, 'bob', 'human', 'which one?', 'answered', 200, 'ask')")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1)")
	for _, ev := range []struct {
		ts        int
		who, kind string
		payload   string
	}{
		{110, "bob", "task_start", `{"task_id": 1}`},
		{200, "bob", "block_task", `{"task_id": 1, "blocks_on": 2}`},
		{320, "bob", "park", `{"kind": "usage"}`},
		{340, "bob", "wake", `{}`},
	} {
		run(t, e, "INSERT INTO events(ts, agent, kind, payload) VALUES(?, ?, ?, ?)", ev.ts, ev.who, ev.kind, ev.payload)
	}
	run(t, e, "INSERT INTO session_starts(agent, ts, cause, resumed, text, prompt) VALUES('bob', 350, 'wake', 1, '', '')")
	got := segmentsOf(t, e, 1, 1000)
	want := []string{"queued queued 100-110", "waiting waiting on you 110-200", "waiting blocked by T2 200-300", "working  300-320",
		"waiting wall 320-340", "starting agent starting 340-350", "working  350-400"}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("segments:\n%s\nwant:\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
}

// An open task runs to now, and a report waits on whoever accepts it.
func TestAnOpenTasksTimeRunsToNowAndAReportWaitsOnItsAccepter(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'amy', 100)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(100, 'bob', 'task_start', '{"task_id": 1}'), (150, 'bob', 'task_report', '{"task_id": 1, "to": "amy"}')`)
	got := segmentsOf(t, e, 1, 200)
	want := "working  100-150\nwaiting waiting on amy 150-200"
	if strings.Join(got, "\n") != want {
		t.Fatalf("segments:\n%s\nwant:\n%s", strings.Join(got, "\n"), want)
	}
}

// Where a founder ask and a blocker overlap, the stretch shows the founder: the wait the founder can end first.
func TestAFounderAskOverABlockerShowsAsTheFounderWait(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'amy', 100), (2, 'b', 'cy', 'bob', 100)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, state_at, kind) VALUES(1, 160, 'bob', 'human', 'which one?', 'answered', 200, 'ask')")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(100, 'bob', 'task_start', '{"task_id": 1}'), (150, 'bob', 'block_task', '{"task_id": 1, "blocks_on": 2}')`)
	got := segmentsOf(t, e, 1, 300)
	want := "working  100-150\nwaiting blocked by T2 150-160\nwaiting waiting on you 160-200\nwaiting blocked by T2 200-300"
	if strings.Join(got, "\n") != want {
		t.Fatalf("segments:\n%s\nwant:\n%s", strings.Join(got, "\n"), want)
	}
}

// Two stretches that touch and read the same are one segment, not two blocks side by side.
func TestTouchingStretchesThatReadTheSameAreOneSegment(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'amy', 100)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, state_at, kind) VALUES(1, 110, 'bob', 'human', 'one?', 'answered', 150, 'ask'), "+
		"(2, 150, 'bob', 'human', 'two?', 'answered', 200, 'ask')")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1), (1, 2)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(100, 'bob', 'task_start', '{"task_id": 1}')`)
	got := segmentsOf(t, e, 1, 250)
	want := "working  100-110\nwaiting waiting on you 110-200\nworking  200-250"
	if strings.Join(got, "\n") != want {
		t.Fatalf("segments:\n%s\nwant:\n%s", strings.Join(got, "\n"), want)
	}
}

// Blocked twice on the same task with no unblock between: one wait, not a first span left open to the end.
func TestASecondBlockOnTheSameTaskDoesNotLeaveTheFirstOpen(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, done_at) VALUES(1, 'a', 'bob', 'amy', 100, 400), (2, 'b', 'cy', 'bob', 100, 300)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(100, 'bob', 'task_start', '{"task_id": 1}'), `+
		`(150, 'bob', 'block_task', '{"task_id": 1, "blocks_on": 2}'), (200, 'bob', 'block_task', '{"task_id": 1, "blocks_on": 2}'), `+
		`(250, 'bob', 'unblock_task', '{"task_id": 1, "blocks_on": 2}')`)
	got := segmentsOf(t, e, 1, 1000)
	want := "working  100-150\nwaiting blocked by T2 150-250\nworking  250-400"
	if strings.Join(got, "\n") != want {
		t.Fatalf("segments:\n%s\nwant:\n%s", strings.Join(got, "\n"), want)
	}
}

// A founder ask closed with no time on record ends by the task's next event, not at the task's end.
func TestAClosedFounderAskWithNoTimeEndsByTheNextEvent(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, done_at) VALUES(1, 'a', 'bob', 'amy', 100, 400)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 120, 'bob', 'human', 'which one?', 'answered', 'ask')")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(100, 'bob', 'task_start', '{"task_id": 1}'), (180, 'bob', 'edit_task', '{"task_id": 1}')`)
	got := segmentsOf(t, e, 1, 1000)
	want := "working  100-120\nwaiting waiting on you 120-180\nworking  180-400"
	if strings.Join(got, "\n") != want {
		t.Fatalf("segments:\n%s\nwant:\n%s", strings.Join(got, "\n"), want)
	}
}
