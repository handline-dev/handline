package hrwrites

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
)

const teamYAML = `teams:
  eng: [alice, bob]
agents:
  alice: {}
  bob: {}
edges:
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
limits:
  max_chain_depth: 4
  ask_rate_per_hour: 5
`

type auth struct{}

func (auth) AgentOf(int) string         { return "" }
func (auth) FounderKeyOK(k string) bool { return k == "founder" }

type rig struct {
	t    *testing.T
	home string
	db   *sql.DB
	e    *Env
	srv  *rpc.Server
	live map[string]bool
}

func newRig(t *testing.T) *rig {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT", "OCP_TERMINAL"} {
		t.Setenv(k, "")
	}
	home := t.TempDir()
	if err := os.WriteFile(filepath.Join(home, "team.yaml"), []byte(teamYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	r := &rig{t: t, home: home, db: d, live: map[string]bool{}}
	r.e = &Env{DB: d, Home: home, Say: &supervisor.Env{DB: d, Home: home}, Live: func() (map[string]bool, bool) { return r.live, true }}
	for _, a := range []string{"alice", "bob"} {
		if _, err := team.NewRoster(home).Register(a, team.RegisterOpts{Role: "r"}); err != nil {
			t.Fatal(err)
		}
	}
	if err := sealed.New(home).Seal(sealed.Team, teamYAML); err != nil {
		t.Fatal(err)
	}
	s := rpc.NewServer(auth{}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = d
	Register(s, r.e)
	r.srv = s
	return r
}

func (r *rig) call(method, params string) map[string]any {
	r.t.Helper()
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { r.srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
	defer func() { cl.Close(); <-done }()
	rd := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(20 * time.Second))
	var m map[string]any
	for _, line := range []string{`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"founder"}}`,
		fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":%s}`, method, params)} {
		cl.Write([]byte(line + "\n"))
		b, err := rd.ReadBytes('\n')
		if err != nil {
			r.t.Fatal(err)
		}
		m = nil
		json.Unmarshal(b, &m)
	}
	return m
}

// propose writes a proposal file as hr.py would and returns it with its wire id.
func (r *rig) propose(id string, fields ...string) (*Prop, int64) {
	r.t.Helper()
	p := &Prop{}
	p.set("id", id)
	for i := 0; i < len(fields); i += 2 {
		p.set(fields[i], fields[i+1])
	}
	p.set("requested_by", "alice")
	p.set("why", "because")
	p.set("created", int64(1000))
	p.set("state", "proposed")
	if err := r.e.save(p); err != nil {
		r.t.Fatal(err)
	}
	n, _ := hexNum(id)
	return p, n
}

func (r *rig) decide(id int64, decision string, subject string, rid string) map[string]any {
	params := fmt.Sprintf(`{"proposal":%d,"decision":%q,"request_id":%q`, id, decision, rid)
	if subject != "" {
		params += fmt.Sprintf(`,"subject":%q`, subject)
	}
	return r.call("hr.decide", params+"}")
}

func (r *rig) file(parts ...string) string {
	b, _ := os.ReadFile(filepath.Join(append([]string{r.home}, parts...)...))
	return string(b)
}

func (r *rig) state(id string) string {
	p, _ := r.e.load(id)
	return p.str("state")
}

func gate(m map[string]any) string {
	e, _ := m["error"].(map[string]any)
	d, _ := e["data"].(map[string]any)
	g, _ := d["gate"].(string)
	return g
}

func TestRejectIsRecordedOnceAndAFixedProposalIsRefusedAgain(t *testing.T) {
	r := newRig(t)
	_, id := r.propose("aaaa0001", "kind", "link", "agent", "alice", "to", "bob")
	res := r.decide(id, "reject", "", "d1")["result"].(map[string]any)
	if res["said"] != "proposal aaaa0001 rejected" || r.state("aaaa0001") != "rejected" {
		t.Fatalf("%v %s", res, r.state("aaaa0001"))
	}
	if got := r.e.decisionOf("aaaa0001"); got["call"] != "rejected" {
		t.Fatalf("the decision is on record: %v", got)
	}
	if again := r.decide(id, "reject", "", "d1")["result"].(map[string]any); again["replayed"] != true {
		t.Fatalf("a retry repeats nothing: %v", again)
	}
	settled := r.decide(id, "reject", "", "d2")
	if gate(settled) != "proposal_settled" {
		t.Fatalf("a settled proposal is refused: %v", settled)
	}
	if e := settled["error"].(map[string]any)["data"].(map[string]any); e["proposal"] == nil {
		t.Fatal("the refusal carries the proposal as it is")
	}
}

func TestApproveNeedsTheSubjectTheFounderReadAndAppliesALink(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa0002", "kind", "link", "agent", "alice", "to", "bob", "response", "async")
	if m := r.call("hr.decide", fmt.Sprintf(`{"proposal":%d,"decision":"approve","request_id":"a0"}`, id)); m["error"] == nil {
		t.Fatal("an approve names its subject")
	}
	if gate(r.decide(id, "approve", "not-the-digest", "a1")) != "proposal_changed" {
		t.Fatal("an edit after the card was drawn is refused")
	}
	if r.state("aaaa0002") != "proposed" || strings.Contains(r.file("team.yaml"), "alice") && strings.Contains(r.file("team.yaml"), `to: "bob"`) {
		t.Fatal("nothing changed")
	}
	res := r.decide(id, "approve", p.subject(), "a2")["result"].(map[string]any)
	if !strings.HasPrefix(res["said"].(string), "alice may now ask bob. team.yaml updated") {
		t.Fatalf("%v", res)
	}
	if !strings.Contains(r.file("team.yaml"), `{ from: "alice", to: "bob", response: async }`) || r.state("aaaa0002") != "applied" {
		t.Fatalf("the edge is in team.yaml and the proposal applied:\n%s", r.file("team.yaml"))
	}
	if !strings.Contains(r.file("team.yaml.bak"), "limits") {
		t.Fatal("the previous team.yaml is kept")
	}
	if gate(r.decide(id, "approve", p.subject(), "a3")) != "proposal_settled" {
		t.Fatal("applied is settled")
	}
}

func TestAddRegistersTheAgentAndPutsItInTeamYaml(t *testing.T) {
	r := newRig(t)
	repo := t.TempDir()
	os.Mkdir(filepath.Join(repo, ".git"), 0o755)
	p, id := r.propose("aaaa0003", "kind", "add", "agent", "carol", "team", "eng", "repo", repo)
	res := r.decide(id, "approve", p.subject(), "b1")["result"].(map[string]any)
	said := res["said"].(string)
	if !strings.HasPrefix(said, "carol is an agent, team eng. team.yaml updated") || !strings.HasSuffix(said, "is a git repository.") {
		t.Fatalf("%q", said)
	}
	yml := r.file("team.yaml")
	if !strings.Contains(yml, "  carol: {}") || !strings.Contains(yml, "eng: [alice, bob, carol]") {
		t.Fatalf("%s", yml)
	}
	if _, err := os.Stat(filepath.Join(r.home, "agents", "carol", "agent.yaml")); err != nil {
		t.Fatal("registered")
	}
}

func TestADiffThatFailsHalfwayPutsEverythingBack(t *testing.T) {
	r := newRig(t)
	before := r.file("team.yaml")
	p, id := r.propose("aaaa0004", "kind", "bundle", "agent", "2 changes",
		"i1.kind", "add", "i1.agent", "dave", "i1.team", "eng",
		"i2.kind", "link", "i2.agent", "dave", "i2.to", "nobody")
	res := r.decide(id, "approve", p.subject(), "c1")
	msg := res["error"].(map[string]any)["message"].(string)
	if !strings.Contains(msg, "step 2 of 2 (link dave) failed") || !strings.Contains(msg, "Nothing was changed") {
		t.Fatalf("%v", res)
	}
	if r.file("team.yaml") != before {
		t.Fatalf("team.yaml put back:\n%s", r.file("team.yaml"))
	}
	if _, err := os.Stat(filepath.Join(r.home, "agents", "dave")); err == nil {
		t.Fatal("the folder the first step made is gone")
	}
	if r.state("aaaa0004") != "approved" {
		t.Fatalf("decided but not applied, so approving again retries: %s", r.state("aaaa0004"))
	}
	got, _ := r.e.load("aaaa0004")
	if got.str("error") == "" {
		t.Fatal("the error is kept on the proposal")
	}
}

func TestRetireArchivesTheFolderAndRenameMovesTheNameAndItsRows(t *testing.T) {
	r := newRig(t)
	r.db.Exec("INSERT INTO agent_state(agent, state, updated) VALUES('bob', 'idle', 1)")
	if _, err := dbpkg.AddRequest(r.db, dbpkg.NewRequest{Requester: "alice", Target: "bob", Body: "hi", Kind: "ask"}); err != nil {
		t.Fatal(err)
	}
	p, id := r.propose("aaaa0005", "kind", "rename", "agent", "bob", "to", "robert")
	r.live["bob"] = true
	if m := r.decide(id, "approve", p.subject(), "e1"); m["error"] == nil || !strings.Contains(m["error"].(map[string]any)["message"].(string), "has a session running") {
		t.Fatalf("not while it runs: %v", m)
	}
	r.live["bob"] = false
	res := r.decide(id, "approve", p.subject(), "e2")["result"].(map[string]any)
	if !strings.HasPrefix(res["said"].(string), "bob is now robert") {
		t.Fatalf("%v", res)
	}
	var target, agent string
	r.db.QueryRow("SELECT target FROM requests WHERE body = 'hi'").Scan(&target)
	r.db.QueryRow("SELECT agent FROM agent_state").Scan(&agent)
	yml := r.file("team.yaml")
	if target != "robert" || agent != "robert" || !strings.Contains(yml, "  robert: {}") || !strings.Contains(yml, "[alice, robert]") || strings.Contains(yml, "bob") {
		t.Fatalf("%s %s\n%s", target, agent, yml)
	}
	pairs, _, _ := yamlcompat.ParsePairs(r.file("agents", "robert", "agent.yaml"))
	if len(pairs) == 0 {
		t.Fatal("the folder moved with the name")
	}
	p2, id2 := r.propose("aaaa0006", "kind", "retire", "agent", "robert")
	res = r.decide(id2, "approve", p2.subject(), "e3")["result"].(map[string]any)
	if !strings.Contains(res["said"].(string), "robert is retired; its folder is at") {
		t.Fatalf("%v", res)
	}
	if _, err := os.Stat(filepath.Join(r.home, "agents", "robert")); err == nil {
		t.Fatal("archived")
	}
	matches, _ := filepath.Glob(filepath.Join(r.home, "agents", "archive", "*-robert"))
	if len(matches) != 1 || strings.Contains(r.file("team.yaml"), "robert") {
		t.Fatalf("%v", matches)
	}
}

func TestContextScheduleGlobalAndSettingsAreAppliedAsProposed(t *testing.T) {
	r := newRig(t)
	ctx, _ := json.Marshal("You are alice.\n")
	p, id := r.propose("aaaa0007", "kind", "context", "agent", "alice", "set.ROLE.md", string(ctx))
	r.decide(id, "approve", p.subject(), "f1")
	if r.file("agents", "alice", "ROLE.md") != "You are alice.\n" {
		t.Fatalf("%q", r.file("agents", "alice", "ROLE.md"))
	}
	p, id = r.propose("aaaa0008", "kind", "schedule", "agent", "alice", "set.action", "add", "set.cron", "0 9 * * *", "set.message", "morning")
	if res := r.decide(id, "approve", p.subject(), "f2"); !strings.Contains(r.file("agents", "alice", "agent.yaml"), "morning") {
		t.Fatalf("scheduled: %v\n%s", res, r.file("agents", "alice", "agent.yaml"))
	}
	p, id = r.propose("aaaa0009", "kind", "global", "agent", "idle-timeout", "set.value", "1800")
	r.decide(id, "approve", p.subject(), "f3")
	if !strings.Contains(r.file("config.yaml"), "park_timeout_s: 1800") {
		t.Fatalf("%q", r.file("config.yaml"))
	}
	p, id = r.propose("aaaa0009b", "kind", "global", "agent", "service-use", "set.value", "go")
	r.decide(id, "approve", p.subject(), "f3b")
	if !strings.Contains(r.file("config.yaml"), "daemon_impl: go") {
		t.Fatalf("%q", r.file("config.yaml"))
	}
	p, id = r.propose("aaaa000a", "kind", "settings", "agent", "alice", "set.effort", "high")
	res := r.decide(id, "approve", p.subject(), "f4")
	if res["result"] == nil || !strings.Contains(r.file("agents", "alice", "agent.yaml"), "effort: high") {
		t.Fatalf("%v\n%s", res, r.file("agents", "alice", "agent.yaml"))
	}
}

func TestAgentSetTakesEveryFieldOfTheContractForTheFounder(t *testing.T) {
	r := newRig(t)
	for _, c := range []struct{ key, val, want string }{
		{"model", "opus", "model: opus"},
		{"effort", "high", "effort: high"},
		{"weight", "heavy", "weight: heavy"},
		{"skip_permissions", "on", "skip_permissions"},
		{"browser", "chrome", "chrome: true"},
		{"context_k", "120", "120"},
		{"idle_close", "30", "park_timeout_s: 1800"},
		{"handoff_note", "off", "handoff_note"},
		{"sandbox", "on", "sandbox"},
		{"mode", "plan", "plan"},
	} {
		res := r.call("agent.set", fmt.Sprintf(`{"agent":"alice","values":{%q:%q},"request_id":"as-%s"}`, c.key, c.val, c.key))
		if got, _ := res["result"].(map[string]any); got["accepted"] != true {
			t.Fatalf("%s=%s: %v", c.key, c.val, res)
		}
		if all := r.file("agents", "alice", "agent.yaml") + r.file("team.yaml"); !strings.Contains(all, c.want) {
			t.Errorf("%s=%s not written (wanted %q):\n%s", c.key, c.val, c.want, all)
		}
	}
	res := r.call("agent.set", `{"agent":"alice","values":{"browser":"playwright","browser_sites":"https://a.example https://b.example"},"request_id":"as-sites"}`)
	if res["result"] == nil || !strings.Contains(r.file("agents", "alice", "agent.yaml")+r.file("agents", "alice", "mcp.json"), "b.example") {
		t.Fatalf("sites: %v", res)
	}
	res = r.call("agent.set", `{"agent":"bob","values":{"effort":"low","context_k":"90"},"request_id":"as-two"}`)
	if res["result"] == nil || !strings.Contains(r.file("agents", "bob", "agent.yaml"), "effort: low") {
		t.Fatalf("%v\n%s", res, r.file("agents", "bob", "agent.yaml"))
	}
}

func TestAgentSetRefusesWhatCannotBeSaved(t *testing.T) {
	r := newRig(t)
	for name, params := range map[string]string{
		"a bad value":    `{"agent":"alice","values":{"effort":"extreme"},"request_id":"b1"}`,
		"an unknown key": `{"agent":"alice","values":{"nope":"x"},"request_id":"b2"}`,
		"no agent":       `{"agent":"ghost","values":{"effort":"high"},"request_id":"b3"}`,
	} {
		if res := r.call("agent.set", params); res["error"] == nil {
			t.Errorf("%s was saved: %v", name, res)
		}
	}
	for name, params := range map[string]string{
		"no values":     `{"agent":"alice","values":{},"request_id":"b4"}`,
		"a number":      `{"agent":"alice","values":{"context_k":150},"request_id":"b5"}`,
		"a missing one": `{"values":{"effort":"high"},"request_id":"b6"}`,
	} {
		res := r.call("agent.set", params)
		if e, _ := res["error"].(map[string]any); e == nil || e["code"] == nil {
			t.Errorf("%s: %v", name, res)
		}
	}
}

func TestAgentSetLeavesTheCustomBrowserToATerminal(t *testing.T) {
	r := newRig(t)
	if res := r.call("agent.set", `{"agent":"alice","values":{"browser":"custom"},"request_id":"c1"}`); res["error"] == nil {
		t.Fatalf("custom was saved: %v", res)
	}
}

func TestAgentSetIsRefusedToAPairedDevice(t *testing.T) {
	r := newRig(t)
	c := &rpc.Conn{Caller: rpc.Caller{Role: "member", Info: map[string]any{"kind": "device", "name": "phone", "device": "d1"}}}
	before := r.file("agents", "alice", "agent.yaml")
	_, err := r.e.agentSetMethod(c, map[string]any{"agent": "alice", "values": map[string]any{"skip_permissions": "on", "sandbox": "off"}})
	var re *rpc.Error
	if !errors.As(err, &re) || re.Data["gate"] != "role" {
		t.Fatalf("a device was not refused at the role gate: %v", err)
	}
	if r.file("agents", "alice", "agent.yaml") != before {
		t.Fatal("a refused device changed the agent")
	}
}
