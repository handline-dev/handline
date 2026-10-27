package reads

import (
	"fmt"
	"reflect"
	"testing"

	"github.com/handline/handline/go/internal/supervisor"
)

func TestEachTaskIsServedWithItsStateAndReason(t *testing.T) {
	e := testEnv(t)
	e.Delivery = wallDelivery{}
	tasks := "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, delivered_at, reported_at, report, done_evidence, done_at, " +
		"discarded_by, discard_reason, discarded_at) VALUES "
	for _, v := range []string{
		"(1, 'draft', 'bob', 'amy', 1, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(2, 'queued', 'bob', 'amy', 1, 'amy', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(3, 'doing', 'bob', 'amy', 1, 'amy', 2, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(4, 'mine', 'human', 'amy', 1, 'amy', 2, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(6, 'done', 'bob', 'amy', 1, 'amy', 2, NULL, NULL, 'x', 4, NULL, NULL, NULL)",
		"(7, 'discarded', 'bob', 'amy', 1, 'amy', NULL, NULL, NULL, NULL, NULL, 'amy', 'dup', 4)",
		"(8, 'blocked', 'bob', 'amy', 1, 'amy', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(9, 'asks', 'bob', 'amy', 1, 'amy', 2, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(10, 'cap', 'bob', 'amy', 1, 'amy', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(11, 'wall', 'bob', 'amy', 1, 'amy', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(12, 'busy', 'bob', 'amy', 1, 'amy', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
		"(13, 'ready', 'bob', 'amy', 1, 'amy', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
	} {
		run(t, e, tasks+v)
	}
	run(t, e, "UPDATE tasks SET sent_at = 2 WHERE id IN (3, 4, 9)")
	run(t, e, "INSERT INTO agent_state(agent, state, updated, current_task) VALUES('bob', 'IDLE', 1, 3)")
	run(t, e, "INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(8, 3, 1792000000)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 1792000000, 'bob', 'human', 'ok?', 'queued', 'ask', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(9, 1)")
	for tid, rec := range map[int]string{
		10: `{"why": "waiting for a slot on a (2 of 2 mid-turn)", "on": "cap", "able": false, "since": 1792000000}`,
		11: `{"why": "waiting for a's 5h window (100%, rolls over 04:00)", "on": "wall", "able": false, "since": 1792000000}`,
		12: `{"why": "bob is mid-turn", "on": "owner", "able": false, "since": 1792000000}`,
		13: fmt.Sprintf(`{"why": %q, "on": "owner", "able": true, "since": 1792000000}`, supervisor.Ready),
	} {
		run(t, e, "INSERT INTO meta(name, value, updated) VALUES(?, ?, 0)", fmt.Sprintf("taskhold:%d", tid), rec)
	}
	since := ts(int64(1792000000))
	for _, c := range []struct {
		id                  int64
		state, reason, text string
		until, since        any
		tasks               []int64
	}{
		{1, "waiting", "opener", "draft: amy has not queued it", nil, ts(int64(1)), nil},
		{2, "preparing", "work", "bob gets it at the next tick", nil, ts(int64(1)), nil},
		{3, "running", "turn", "bob has it", nil, ts(int64(2)), nil},
		{4, "waiting", "you", "yours to do", nil, ts(int64(1)), nil},
		{6, "done", "done", "done", nil, ts(int64(4)), nil},
		{7, "done", "discarded", "discarded: dup", nil, ts(int64(4)), nil},
		{8, "waiting", "task", "blocked by T3", nil, since, []int64{3}},
		{9, "waiting", "you", "needs you", nil, since, nil},
		{10, "stopped", "cap", "waiting for a slot on a (2 of 2 mid-turn)", nil, since, nil},
		{11, "stopped", "wall", "waiting for a's 5h window (100%, rolls over 04:00)", ts(int64(1700)), since, nil},
		{12, "waiting", "owner", "bob is mid-turn", nil, since, nil},
		{13, "preparing", "work", "bob gets it at the next tick", nil, since, nil},
	} {
		task, _ := clockOf(t, e, c.id)
		want := map[string]any{"state": c.state, "reason": c.reason, "text": c.text, "until": c.until, "since": c.since, "tasks": c.tasks}
		if c.tasks == nil {
			want["tasks"] = []int64{}
		}
		if !reflect.DeepEqual(task["status"], want) {
			t.Errorf("T%d: %v; want %v", c.id, task["status"], want)
		}
	}
}
