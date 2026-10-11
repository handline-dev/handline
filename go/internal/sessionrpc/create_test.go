package sessionrpc

import (
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/operations"
	"github.com/handline/handline/go/internal/team"
)

type seen struct {
	mu  sync.Mutex
	got []map[string]any
}

func (s *seen) emit(_, typ string, data any, _ string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if typ == "operation.progress" {
		s.got = append(s.got, data.(map[string]any))
	}
}

func (s *seen) states() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []string
	for _, d := range s.got {
		out = append(out, d["state"].(string))
	}
	return out
}

func createRig(t *testing.T) (*rig, *seen, *conn) {
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} { // Register is the founder's: not from inside an agent's session
		t.Setenv(k, "")
	}
	r := newRig(t)
	sn := &seen{}
	r.e.Roster, r.e.Emit = team.NewRoster(r.e.Home), sn.emit
	return r, sn, r.dial(false)
}

// waitEnded waits until the operation's row has left running and its goroutine has returned.
func waitEnded(t *testing.T, r *rig, op string) {
	t.Helper()
	for i := 0; i < 250; i++ {
		var state string
		if err := r.e.DB.QueryRow("SELECT state FROM operations WHERE id = ?", op).Scan(&state); err == nil && state != "running" && !operations.Live(op) {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("the operation did not end")
}

func TestCreateRegistersAndStartsAndTellsEachStep(t *testing.T) {
	r, sn, c := createRig(t)
	repo := t.TempDir()
	got := result(t, c.call("agent.create", map[string]any{"name": "ann.new", "role": "worker", "repo": repo, "parent": "ann", "effort": "high", "request_id": rid("c1")}))
	op, _ := got["operation"].(string)
	if got["accepted"] != true || op == "" {
		t.Fatalf("%v", got)
	}
	waitEnded(t, r, op)
	if len(sn.got) > 0 && sn.got[len(sn.got)-1]["state"] == "failed" {
		t.Fatalf("%v", sn.got[len(sn.got)-1])
	}
	if r.ctl.calls[len(r.ctl.calls)-1] != "start ann.new "+repo+" main true" {
		t.Fatalf("started through the session path: %v", r.ctl.calls)
	}
	cfg, err := team.NewRoster(r.e.Home).Read("ann.new")
	if err != nil || cfg["effort"] != "high" || cfg["repo"] != repo {
		t.Fatalf("registered with what was asked: %v %v", cfg, err)
	}
	if st := sn.states(); st[0] != "running" || st[len(st)-1] != "done" {
		t.Fatalf("%v", st)
	}
	last := sn.got[len(sn.got)-1]
	if res := last["result"].(map[string]any); res["agent"] != "ann.new" || res["registered"] != true || last["agent"] != "ann.new" {
		t.Fatalf("%v", last)
	}
	again := result(t, c.call("agent.create", map[string]any{"name": "ann.new", "role": "worker", "repo": repo, "parent": "ann", "effort": "high", "request_id": rid("c1")}))
	if again["replayed"] != true || again["operation"] != op || len(r.ctl.calls) != 1 {
		t.Fatalf("a retry starts nothing twice: %v %v", again, r.ctl.calls)
	}
}

func TestCreateWhoseStartFailsLeavesTheAgentRegistered(t *testing.T) {
	r, sn, c := createRig(t)
	r.ctl.ok, r.ctl.said = false, "diverged"
	repo := t.TempDir()
	op := result(t, c.call("agent.create", map[string]any{"name": "solo", "role": "worker", "repo": repo, "request_id": rid("c2")}))["operation"].(string)
	waitEnded(t, r, op)
	last := sn.got[len(sn.got)-1]
	res := last["result"].(map[string]any)
	if last["state"] != "failed" || res["registered"] != true || last["message"] != "solo is registered; starting it failed: diverged" {
		t.Fatalf("%v", last)
	}
	if _, err := team.NewRoster(r.e.Home).Read("solo"); err != nil {
		t.Fatalf("nothing is cleaned up: %v", err)
	}
}

func TestCreateRefusesWhatItCannotStart(t *testing.T) {
	r, _, c := createRig(t)
	repo := t.TempDir()
	ok := map[string]any{"name": "x", "role": "worker", "repo": repo}
	with := func(k string, v any, id string) map[string]any {
		p := map[string]any{"request_id": rid(id)}
		for a, b := range ok {
			p[a] = b
		}
		p[k] = v
		return c.call("agent.create", p)
	}
	if g := refused(with("name", "ann", "u1")); g != "state" {
		t.Fatalf("a used name: %q", g)
	}
	if f := badField(with("parent", "ann", "u2")); f != "name" {
		t.Fatalf("a name outside its parent: %q", f)
	}
	if f := badField(with("effort", "extreme", "u3")); f != "effort" {
		t.Fatalf("%q", f)
	}
	if f := badField(with("account", "a1", "u4")); f != "account" {
		t.Fatalf("%q", f)
	}
	if f := badField(with("name", "a/b", "u5")); f != "name" {
		t.Fatalf("%q", f)
	}
	if m := with("repo", "/no/such/repo", "u6"); m["error"].(map[string]any)["data"].(map[string]any)["what"] != "repo" {
		t.Fatalf("%v", m)
	}
	if m := with("parent", "nobody", "u7"); m["error"].(map[string]any)["data"].(map[string]any)["what"] != "agent" {
		t.Fatalf("%v", m)
	}
	if len(r.ctl.calls) != 0 {
		t.Fatalf("nothing started: %v", r.ctl.calls)
	}
}

func TestCreateRefusesASecondOfTheNameWhileTheFirstRuns(t *testing.T) {
	r, _, c := createRig(t)
	if _, err := r.e.DB.Exec("INSERT INTO operations(id, kind, state, steps_done, agent, created_at, updated_at) VALUES('held', 'agent.create', 'running', 0, 'busy', 0, 0)"); err != nil {
		t.Fatal(err)
	}
	operations.Begin("held")
	defer operations.End("held")
	if g := refused(c.call("agent.create", map[string]any{"name": "busy", "role": "worker", "repo": t.TempDir(), "request_id": rid("b1")})); g != "state" {
		t.Fatalf("%q", g)
	}
}
