package legacyverbs

import (
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/sessionrpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/supervisor"
)

const teamYAML = `teams:
  eng: [alice, bob]
agents:
  alice: {}
  bob: {}
edges:
  - { from: "*", to: "human", kind: task }
  - { from: "*", to: "*", relation: peer, response: async }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
`

type fakeProposals struct{}

func (fakeProposals) OpenProposalOf(int64) string                             { return "" }
func (fakeProposals) Answered(dbpkg.Q, string, *dbpkg.Request, string) string { return "" }

type fakeEval struct{}

func (fakeEval) QueuedOrIdle(dbpkg.Q, string, string) error { return nil }

// controls records the calls of a close or restart.
type controls struct {
	sessionrpc.Controls
	calls []string
	boom  bool
}

func (c *controls) Close(call sessionrpc.Call, agent string, fresh bool) (bool, string, error) {
	if c.boom {
		panic("boom")
	}
	c.calls = append(c.calls, "close "+agent+" by "+call.By+map[bool]string{true: " fresh", false: ""}[fresh]+map[bool]string{true: " person", false: ""}[call.Person])
	return true, agent + ": closed", nil
}

func (c *controls) Restart(call sessionrpc.Call, agent string, fresh, force bool) (bool, string, error) {
	c.calls = append(c.calls, "restart "+agent+" by "+call.By+map[bool]string{true: " fresh", false: ""}[fresh]+map[bool]string{true: " force", false: ""}[force])
	return false, agent + ": not restarted - busy", nil
}

type rig struct {
	t    *testing.T
	db   *sql.DB
	e    *Env
	ctl  *controls
	sets []string
}

func newRig(t *testing.T) *rig {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} {
		t.Setenv(k, "")
	}
	supervisor.RunInline = true
	t.Cleanup(func() { supervisor.RunInline = false })
	home := t.TempDir()
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte(teamYAML), 0o644)
	for _, a := range []string{"alice", "bob"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	}
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	r := &rig{t: t, db: d, ctl: &controls{}}
	sup := &supervisor.Env{DB: d, Home: home, Log: func(string, ...any) {}, Ports: supervisor.Ports{Proposals: fakeProposals{}, Evaluate: fakeEval{}}}
	r.e = &Env{DB: d, Home: home, Sup: sup, Controls: func() sessionrpc.Controls { return r.ctl },
		SetByAgent: func(agent string, values map[string]string, speaker string) (bool, string) {
			b, _ := json.Marshal(values)
			r.sets = append(r.sets, agent+" "+string(b)+" by "+speaker)
			return true, "saved"
		}}
	return r
}

// ask sends one legacy request as the wire carries it: decoded with numbers kept.
func (r *rig) ask(line string) map[string]any {
	r.t.Helper()
	dec := json.NewDecoder(strings.NewReader(line))
	dec.UseNumber()
	var p map[string]any
	if err := dec.Decode(&p); err != nil {
		r.t.Fatal(err)
	}
	return r.e.Legacy(p, 0)
}

func (r *rig) count(q string, args ...any) (n int) {
	r.t.Helper()
	if err := r.db.QueryRow(q, args...).Scan(&n); err != nil {
		r.t.Fatal(err)
	}
	return n
}

func TestSayWritesTheRowAndAnswersLikeThePythonVerb(t *testing.T) {
	r := newRig(t)
	got := r.ask(`{"verb":"say","requester":"alice","speaker":"alice","target":"bob","body":"psst","parent":null,"task":null,"no_task":null}`)
	if got["ok"] != true || !strings.Contains(got["said"].(string), "bob") || r.count("SELECT COUNT(*) FROM requests WHERE requester='alice' AND target='bob' AND body='psst'") != 1 {
		t.Fatalf("%v", got)
	}
	if got := r.ask(`{"verb":"say","target":"bob","body":"x"}`); got["ok"] != false || !strings.Contains(got["said"].(string), "cannot tell who is asking") {
		t.Fatalf("a say that names nobody: %v", got)
	}
	if got := r.ask(`{"verb":"say","requester":"alice"}`); got["said"] != "say: 'target'" {
		t.Fatalf("a missing key reads as Python's KeyError: %v", got)
	}
}

func TestPushesAreCheckedBeforeAnythingIsHandedOn(t *testing.T) {
	r := newRig(t)
	for line, said := range map[string]string{
		`{"verb":"push","target":"human"}`:           "human is not an agent",
		`{"verb":"push","target":"nobody"}`:          "nobody is not an agent",
		`{"verb":"push_answer","requester":"human"}`: "human is not an agent",
		`{"verb":"notify_cancelled","rid":99}`:       "no request #99",
	} {
		if got := r.ask(line); got["ok"] != false || got["said"] != said {
			t.Errorf("%s: %v", line, got)
		}
	}
}

func TestCloseAndRestartRunTheSessionControlsAsPythonCalledThem(t *testing.T) {
	r := newRig(t)
	if got := r.ask(`{"verb":"close","agent":"bob","fresh":true}`); got["ok"] != true || got["said"] != "bob: closed" {
		t.Fatalf("%v", got)
	}
	if got := r.ask(`{"verb":"restart","agent":"bob","fresh":false,"force":true,"by":"alice"}`); got["ok"] != false || got["said"] != "bob: not restarted - busy" {
		t.Fatalf("%v", got)
	}
	r.ask(`{"verb":"restart","agent":"bob"}`)
	want := []string{"close bob by human fresh", "restart bob by alice force", "restart bob by human"}
	if strings.Join(r.ctl.calls, "|") != strings.Join(want, "|") {
		t.Fatalf("%v", r.ctl.calls)
	}
	if got := r.ask(`{"verb":"close","agent":"ghost"}`); got["ok"] != false || got["said"] != "no such agent: ghost" {
		t.Fatalf("%v", got)
	}
	r.ctl.boom = true
	if got := r.ask(`{"verb":"close","agent":"bob"}`); got["ok"] != false || got["said"] != "close: boom" {
		t.Fatalf("a verb that fails still replies: %v", got)
	}
}

func TestAgentSetPassesTheValuesAsTextAndTheSpeakerAsSaid(t *testing.T) {
	r := newRig(t)
	if got := r.ask(`{"verb":"agent_set","agent":"bob","values":{"context_k":150,"model":"m"},"speaker":"bob"}`); got["ok"] != true || got["said"] != "saved" {
		t.Fatalf("%v", got)
	}
	r.ask(`{"verb":"agent_set","agent":"bob","values":null,"speaker":null}`)
	if len(r.sets) != 2 || r.sets[0] != `bob {"context_k":"150","model":"m"} by bob` || r.sets[1] != `bob {} by ` {
		t.Fatalf("%v", r.sets)
	}
}

func TestAVerbNothingSendsAnyMoreIsLeftToTheServer(t *testing.T) {
	r := newRig(t)
	for _, v := range []string{"message", "wake", "park", "fold", "move", "start", "trust", "land", "nonsense"} {
		if got := r.ask(`{"verb":"` + v + `"}`); got != nil {
			t.Errorf("%s: %v", v, got)
		}
	}
	r.e.Prompts, r.e.SetByAgent = nil, nil
	if r.ask(`{"verb":"answer_block","block":1}`) != nil || r.ask(`{"verb":"agent_set","agent":"bob"}`) != nil {
		t.Fatal("a verb whose port is missing is not served")
	}
}
