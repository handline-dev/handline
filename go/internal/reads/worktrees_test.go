package reads

import (
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/operations"
)

func TestAWorktreeLabelStandsInForTheDaemonsWord(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "alice", "")
	wt, _ := filepath.EvalSymlinks(t.TempDir())
	if _, err := e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 0, ?)", wt); err != nil {
		t.Fatal(err)
	}
	status := func() string {
		got, err := e.worktreesList(nil, map[string]any{})
		if err != nil {
			t.Fatal(err)
		}
		return got.(map[string]any)["worktrees"].([]any)[0].(map[string]any)["status"].(string)
	}
	if s := status(); s != "clean" {
		t.Fatalf("no label: %q", s)
	}
	key, ok, err := e.LabelKey(wt)
	if err != nil || !ok {
		t.Fatalf("%v %v", ok, err)
	}
	if _, err := e.DB.Exec("INSERT INTO meta(name, value, updated) VALUES(?, 'in review', 0)", key); err != nil {
		t.Fatal(err)
	}
	if s := status(); s != "in review" {
		t.Fatalf("labelled: %q", s)
	}
}

func TestARunningOperationNobodyRunsIsReportedFailed(t *testing.T) {
	e := testEnv(t)
	if _, err := e.DB.Exec("INSERT INTO operations(id, kind, state, steps_done, agent, created_at, updated_at) VALUES('gone', 'agent.create', 'running', 0, 'x', 0, 0), ('here', 'agent.create', 'running', 0, 'y', 0, 0)"); err != nil {
		t.Fatal(err)
	}
	operations.Begin("here")
	defer operations.End("here")
	for id, want := range map[string]string{"gone": "failed", "here": "running"} {
		got, err := e.operationGet(nil, map[string]any{"operation": id})
		if err != nil {
			t.Fatal(err)
		}
		if s := got.(map[string]any)["state"]; s != want {
			t.Fatalf("%s: %v, want %s", id, s, want)
		}
	}
}
