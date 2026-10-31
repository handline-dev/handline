package supervisor

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/taskwrites"
)

type agentAuth struct{ agent string }

func (a agentAuth) AgentOf(int) string       { return a.agent }
func (a agentAuth) SessionsReadable() error  { return nil }
func (a agentAuth) FounderKeyOK(string) bool { return false }

// callAs serves one connection as agent over the task methods with the rig's Env as the teller, and returns the reply to call.
func (r *rig) callAs(agent, call string) map[string]any {
	r.t.Helper()
	srv := rpc.NewServer(agentAuth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	srv.DB = r.DB
	taskwrites.Register(srv, r.home, r.Env, nil)
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
	defer func() { cl.Close(); <-done }()
	in := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(10 * time.Second))
	var out map[string]any
	for _, line := range []string{`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1}}`, call} {
		if _, err := cl.Write([]byte(line + "\n")); err != nil {
			r.t.Fatal(err)
		}
		b, err := in.ReadBytes('\n')
		if err != nil {
			r.t.Fatal(err)
		}
		out = nil
		json.Unmarshal(b, &out)
	}
	return out
}

func (r *rig) founderOnlyTeam() {
	r.t.Helper()
	if err := os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(founderOnlyTeam), 0o644); err != nil {
		r.t.Fatal(err)
	}
}

// An agent's handoff through task.handoff is no system step: on a team with no edge alice -> bob it is refused and the task stays.
func TestAnAgentsHandoffStillNeedsItsEdge(t *testing.T) {
	r := newRig(t)
	r.founderOnlyTeam()
	tid := r.newTask("alice", "human", "job")
	got := r.callAs("alice", fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.handoff","params":{"task":%d,"to":"bob","note":"yours","request_id":"h1"}}`, tid))
	if task, _ := db.GetTask(r.DB, tid); task == nil || task.Owner != "alice" || !strings.Contains(fmt.Sprint(got), "no edge declares alice -> bob") {
		t.Fatalf("refused, and the task stays: %v %+v", got, task)
	}
}

// A system ask is let past a missing edge only to an agent the team knows.
func TestASystemAskToAnUnknownTargetIsRefused(t *testing.T) {
	r := newRig(t)
	r.founderOnlyTeam()
	tx, _ := r.DB.Begin()
	defer tx.Rollback()
	asked, err := r.TaskAsk(tx, taskwrites.AskReq{From: "daemon", To: "nobody.such", Kind: "task", Body: "x", System: true,
		Within: func(int64) error { return fmt.Errorf("not reached") }})
	if err != nil || asked.OK || !strings.Contains(asked.Msg, "unknown target") {
		t.Fatalf("%+v %v", asked, err)
	}
}

// The start-up end of reviews is a system step too: a task left in review goes to its opener though no edge leads from the daemon.
func TestEndReviewsMovesATaskWithNoTeamEdgeFromTheDaemon(t *testing.T) {
	r := newRig(t)
	r.founderOnlyTeam()
	tid := r.newTask("bob", "alice", "reviewed")
	if _, err := r.DB.Exec("UPDATE tasks SET reported_at = 5, report = 'built it' WHERE id = ?", tid); err != nil {
		t.Fatal(err)
	}
	said, err := taskwrites.EndReviews(r.DB, r.home, r.Env)
	if err != nil || fmt.Sprint(said) != fmt.Sprintf("[T%d left review: handed to alice]", tid) {
		t.Fatalf("%q %v", said, err)
	}
	if task, _ := db.GetTask(r.DB, tid); task == nil || task.Owner != "alice" {
		t.Fatalf("%+v", task)
	}
}
