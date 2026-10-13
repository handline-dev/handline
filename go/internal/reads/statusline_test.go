package reads

import (
	"errors"
	"reflect"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
)

func listed(t *testing.T, e *Env, params map[string]any) map[string]map[string]any {
	t.Helper()
	got, err := e.agentsList(nil, params)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]map[string]any{}
	for _, a := range got.(map[string]any)["agents"].([]map[string]any) {
		out[a["name"].(string)] = a
	}
	return out
}

func TestAgentsListWithStatusAddsWhatHlTeamDraws(t *testing.T) {
	e := testEnv(t)
	for _, a := range []string{"ann", "bob", "cat", "dan"} {
		addAgent(t, e, a, "")
	}
	writeAgentYAML(t, e, "ann", "role: r\nrepo: handline\n")
	writeAgentYAML(t, e, "dan", "- not a map\n")
	run(t, e, "INSERT INTO agent_state(agent, state, updated, terminal, closed_at) VALUES"+
		"('ann', 'IDLE', 0, 'h1', NULL), ('bob', 'PARKED', 0, NULL, 5), ('dan', 'IDLE', 0, 'h4', NULL)")
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('ann', '{"name": "ann", "state": "waiting", "state_detail": "a question"}', 0)`)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 1, 'human', 'ann', 'b', 'queued', 'say', 1)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, priority, done_evidence) VALUES"+
		"(3, 'late', 'ann', 'human', 1, 1, NULL), (2, 'early', 'ann', 'human', 1, 2, NULL), (4, 'done', 'ann', 'human', 1, 2, 'sha'), "+
		"(5, 'no hold', 'ann', 'human', 1, 2, NULL), (6, 'another owner', 'bob', 'human', 1, 2, NULL)")
	run(t, e, `INSERT INTO meta(name, value, updated) VALUES('taskhold:2', '{"why": "blocked by T9 (cat)", "able": false}', 0), ('taskhold:3', '{"why": "behind T2"}', 0), `+
		`('taskhold:4', '{"why": "ended"}', 0), ('taskhold:6', '{"why": "bob is closed"}', 0)`)
	e.Delivery = fakeDelivery{wakeWait: map[string]string{"bob": "waiting for alpha's window at 14:00"}}
	got := listed(t, e, map[string]any{"status": true})
	status := func(name string) map[string]any {
		out := map[string]any{}
		for _, k := range []string{"started", "has_repo", "has_terminal", "screen_word", "screen_detail", "unreached", "task_waits", "wake_wait_reason"} {
			out[k] = got[name][k]
		}
		return out
	}
	for name, want := range map[string]map[string]any{
		"ann": {"started": true, "has_repo": true, "has_terminal": true, "screen_word": "DIALOG", "screen_detail": "a question", "unreached": 1,
			"task_waits":       []any{map[string]any{"task": int64(2), "words": "blocked by T9 (cat)"}, map[string]any{"task": int64(3), "words": "behind T2"}},
			"wake_wait_reason": nil},
		"bob": {"started": true, "has_repo": false, "has_terminal": false, "screen_word": "IDLE", "screen_detail": "", "unreached": 0,
			"task_waits": []any{map[string]any{"task": int64(6), "words": "bob is closed"}}, "wake_wait_reason": "waiting for alpha's window at 14:00"},
		"cat": {"started": false, "has_repo": false, "has_terminal": false, "screen_word": nil, "screen_detail": "", "unreached": 0,
			"task_waits": []any{}, "wake_wait_reason": nil},
		"dan": {"started": true, "has_repo": false, "has_terminal": true, "screen_word": "IDLE", "screen_detail": "", "unreached": 0,
			"task_waits": []any{}, "wake_wait_reason": nil},
	} {
		if !reflect.DeepEqual(status(name), want) {
			t.Errorf("%s: %v", name, status(name))
		}
	}
}

func TestAgentsListSendsTheStatusLineOnlyWhenAsked(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "ann", "")
	for _, params := range []map[string]any{nil, {"status": false}} {
		if _, has := listed(t, e, params)["ann"]["started"]; has {
			t.Fatalf("%v: a status line nobody asked for", params)
		}
	}
	var re *rpc.Error
	for _, v := range []any{1, "true", nil} {
		if _, err := e.agentsList(nil, map[string]any{"status": v}); !errors.As(err, &re) || re.RPCCode != rpc.InvalidParams {
			t.Fatalf("status %v: %v", v, err)
		}
	}
}
