package reads

import (
	"testing"
	"time"
)

func expectedOf(t *testing.T, e *Env, tid, now int64) map[string]any {
	t.Helper()
	got, err := e.taskTimeAt(tid, now)
	if err != nil {
		t.Fatal(err)
	}
	x, _ := got.(map[string]any)["expected"].(map[string]any)
	return x
}

func at(sec int64) string { return time.Unix(sec, 0).UTC().Format("2006-01-02T15:04:05Z") }

// Each task queued ahead of it, in the order the daemon hands them out, moves its expected start and end later by that task's expected time.
func TestTheExpectedEndMovesLaterAsTheQueueAheadGrows(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO agent_state(agent, state, updated, account) VALUES('bob', 'IDLE', 1, 'main')")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, priority, timeout_s) VALUES(5, 'mine', 'bob', 'amy', 1000, 'amy', 2, 600)")
	now := int64(2000)
	x := expectedOf(t, e, 5, now)
	if x["start"] != at(now) || x["end"] != at(now+600) || x["basis"] != "time_box" || len(x["ahead"].([]int64)) != 0 {
		t.Fatalf("nothing ahead: it starts now and takes its time box: %v", x)
	}
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, priority, timeout_s) VALUES(3, 'older', 'bob', 'amy', 900, 'amy', 2, 300)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, priority, timeout_s) VALUES(9, 'urgent', 'bob', 'amy', 1500, 'amy', 1, 100)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, priority, timeout_s) VALUES(12, 'behind', 'bob', 'amy', 1600, 'amy', 2, 5000)")
	x = expectedOf(t, e, 5, now)
	if x["start"] != at(now+400) || x["end"] != at(now+1000) || len(x["ahead"].([]int64)) != 2 {
		t.Fatalf("an older task and a higher priority one go first, one behind it does not: %v", x)
	}
	// the owner's current task counts by what is left of it
	run(t, e, "UPDATE agent_state SET current_task = 3 WHERE agent = 'bob'")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(1900, 'bob', 'task_start', '{"task_id": 3}')`)
	if x = expectedOf(t, e, 5, now); x["start"] != at(now+200+100) {
		t.Fatalf("100s of the current task's 300 are worked: %v", x)
	}
}

// The task being worked on started when its work did and ends what is left of it from now, never in the past.
func TestATaskBeingWorkedOnEndsWhatIsLeftOfItFromNow(t *testing.T) {
	e := testEnv(t)
	e.AccountWall = func(string, int64) (int64, int64, int64) { return 0, 0, 0 }
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, timeout_s) VALUES(1, 'a', 'bob', 'amy', 900, 'amy', 3600)")
	run(t, e, "INSERT INTO agent_state(agent, state, updated, account, current_task) VALUES('bob', 'BUSY', 1, 'main', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(1000, 'bob', 'task_start', '{"task_id": 1}')`)
	now := int64(4000)
	if x := expectedOf(t, e, 1, now); x["start"] != at(1000) || x["end"] != at(now+600) {
		t.Fatalf("3000s of 3600 are worked: %v", x)
	}
	e.AccountWall = func(string, int64) (int64, int64, int64) { return now + 100, 0, 0 }
	if x := expectedOf(t, e, 1, now); x["start"] != at(1000) || x["end"] != at(now+100+600) || x["wall_until"] != at(now+100) {
		t.Fatalf("at the limit, the rest waits for the reset: %v", x)
	}
}

// The owner's account at its limit holds the start to the reset; one that runs out before the end holds the rest of the work to its reset.
func TestAnAccountAtItsLimitPushesTheStartToItsReset(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO agent_state(agent, state, updated, account) VALUES('bob', 'IDLE', 1, 'main')")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, timeout_s) VALUES(1, 'a', 'bob', 'amy', 1000, 'amy', 600)")
	now := int64(2000)
	e.AccountWall = func(acct string, at int64) (int64, int64, int64) {
		if acct != "main" || at != now {
			t.Fatalf("asked of %s at %d", acct, at)
		}
		return now + 3600, 0, 0
	}
	if x := expectedOf(t, e, 1, now); x["start"] != at(now+3600) || x["end"] != at(now+3600+600) || x["wall_until"] != at(now+3600) {
		t.Fatalf("%v", x)
	}
	e.AccountWall = func(string, int64) (int64, int64, int64) { return 0, now + 200, now + 1000 }
	if x := expectedOf(t, e, 1, now); x["start"] != at(now) || x["end"] != at(now+600+800) || x["wall_until"] != at(now+1000) {
		t.Fatalf("200s are worked before the runout, the other 400 after the reset: %v", x)
	}
	e.AccountWall = func(string, int64) (int64, int64, int64) { return 0, now + 5000, now + 6000 }
	if x := expectedOf(t, e, 1, now); x["end"] != at(now+600) || x["wall_until"] != nil {
		t.Fatalf("a runout after the end changes nothing: %v", x)
	}
}

// How long done tasks of its size took is its expected duration; with none of its size, their time per point; with none sized, its time box.
func TestHistoryOfEstimateVersusActualBySizeChangesTheExpectedEnd(t *testing.T) {
	e := testEnv(t)
	e.AccountWall = func(string, int64) (int64, int64, int64) { return 0, 0, 0 }
	run(t, e, "INSERT INTO agent_state(agent, state, updated, account) VALUES('bob', 'IDLE', 1, 'main')")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, estimate, timeout_s) VALUES(10, 'new', 'bob', 'amy', 5000, 'amy', 3, 7200)")
	now := int64(9000)
	if x := expectedOf(t, e, 10, now); x["duration_s"] != int64(7200) || x["basis"] != "time_box" {
		t.Fatalf("no history: its time box: %v", x)
	}
	done := func(id, points, worked int64) {
		run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, estimate, done_at, done_evidence) VALUES(?, 'old', 'cy', 'amy', 100, ?, ?, 'x')", id, points, 200+worked)
		run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(200, 'cy', 'task_start', json_object('task_id', ?))`, id)
	}
	done(1, 5, 1000) // 200 a point
	if x := expectedOf(t, e, 10, now); x["duration_s"] != int64(600) || x["basis"] != "points" || x["end"] != at(now+600) {
		t.Fatalf("none of its size: 3 points at 200 a point: %v", x)
	}
	done(2, 3, 900)
	done(3, 3, 1500)
	done(4, 3, 3000)
	if x := expectedOf(t, e, 10, now); x["duration_s"] != int64(1500) || x["basis"] != "history" || x["end"] != at(now+1500) {
		t.Fatalf("the median of its size's three: %v", x)
	}
	if x := expectedOf(t, e, 2, now); x != nil {
		t.Fatalf("an ended task expects nothing: %v", x)
	}
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, timeout_s) VALUES(20, 'yours', 'human', 'amy', 5000, 60)")
	if x := expectedOf(t, e, 20, now); x != nil {
		t.Fatalf("a task the founder holds has no queue or account: %v", x)
	}
}
