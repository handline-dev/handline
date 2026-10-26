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
	"reflect"
	"slices"
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
  - { from: "human", to: "*" }
limits:
  max_chain_depth: 4
  ask_rate_per_hour: 5
`

type auth struct{}

func (auth) AgentOf(int) string         { return "" }
func (auth) FounderKeyOK(k string) bool { return k == "founder" }

type rig struct {
	t      *testing.T
	home   string
	db     *sql.DB
	e      *Env
	device string // when set, calls arrive as this paired device
	srv    *rpc.Server
	live   map[string]bool
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
	return r.callHello(`{"client":"t","protocol":1,"key":"founder"}`, method, params)
}

type agentAuth struct{ name string }

func (a agentAuth) AgentOf(int) string     { return a.name }
func (agentAuth) SessionsReadable() error  { return nil }
func (agentAuth) FounderKeyOK(string) bool { return false }

// callAs is call from the session of agent: its hello carries no key.
func (r *rig) callAs(agent, method, params string) map[string]any {
	r.t.Helper()
	was := r.srv
	defer func() { r.srv = was }()
	s := rpc.NewServer(agentAuth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = r.db
	Register(s, r.e)
	r.srv = s
	return r.callHello(`{"client":"t","protocol":1}`, method, params)
}

func (r *rig) callHello(hello, method, params string) map[string]any {
	r.t.Helper()
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() {
		if r.device != "" {
			r.srv.ServeRelayed(rpc.NewLineTransport(sv), r.device, "phone")
		} else {
			r.srv.Serve(rpc.NewLineTransport(sv), 0)
		}
		sv.Close()
		close(done)
	}()
	defer func() { cl.Close(); <-done }()
	rd := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(20 * time.Second))
	var m map[string]any
	for _, line := range []string{fmt.Sprintf(`{"jsonrpc":"2.0","id":1,"method":"hello","params":%s}`, hello),
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

// hr.list reads back the founder's note on a decision, and no note when none was given.
func TestTheFoundersNoteOnADecisionIsListed(t *testing.T) {
	r := newRig(t)
	_, noted := r.propose("aaaa0001", "kind", "link", "agent", "alice", "to", "bob")
	_, bare := r.propose("aaaa0002", "kind", "link", "agent", "bob", "to", "alice")
	r.call("hr.decide", fmt.Sprintf(`{"proposal":%d,"decision":"reject","note":"not before the redesign","request_id":"d1"}`, noted))
	r.decide(bare, "reject", "", "d2")
	if v := r.e.reader().HRView("aaaa0001"); v["note"] != "not before the redesign" {
		t.Fatalf("%v", v)
	}
	if v := r.e.reader().HRView("aaaa0002"); v["state"] != "rejected" || v["note"] != nil {
		t.Fatalf("%v", v)
	}
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
	if !strings.Contains(r.file("team.yaml"), `{ from: "alice", to: "bob" }`) || r.state("aaaa0002") != "applied" {
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

// withOldEdge gives team.yaml an alice -> bob edge with the relation and response keys links no longer have, and returns it.
func (r *rig) withOldEdge() string {
	r.t.Helper()
	return r.withEdges(`  - { from: "alice", to: "bob", relation: peer, response: async }` + "\n")
}

// withEdges puts edges (whole lines) first in team.yaml's edges and returns the file.
func (r *rig) withEdges(edges string) string {
	r.t.Helper()
	yml := strings.Replace(teamYAML, "edges:\n", "edges:\n"+edges, 1)
	if err := os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(yml), 0o644); err != nil {
		r.t.Fatal(err)
	}
	if err := sealed.New(r.home).Seal(sealed.Team, yml); err != nil {
		r.t.Fatal(err)
	}
	return yml
}

// A bundle applies its unlinks before its links, so an unlink and a link of the same edge rewrite it to from and to alone.
func TestABundleRewritesAnEdgeByUnlinkingThenLinkingIt(t *testing.T) {
	r := newRig(t)
	r.withOldEdge()
	if tm, err := team.LoadTeam(r.home); err != nil || !tm.Route("alice", "bob", 0).Allowed {
		t.Fatalf("the old keys load: %v", err)
	}
	m := r.callAs("alice", "hr.propose", `{"kind":"bundle","args":{"name":"2 changes","changes":[{"kind":"link","name":"alice","to":"bob"},`+
		`{"kind":"unlink","name":"alice","to":"bob"}]},"why":"drop the old keys","request_id":"p1"}`)
	n, ok := m["result"].(map[string]any)["proposal"].(float64)
	if !ok {
		t.Fatalf("the link counts the bundle's unlink: %v", m)
	}
	p, err := r.e.load(fmt.Sprintf("%08x", int64(n)))
	if err != nil {
		t.Fatal(err)
	}
	if res := r.decide(int64(n), "approve", p.subject(), "d1"); res["error"] != nil {
		t.Fatalf("%v", res)
	}
	yml := r.file("team.yaml")
	if !strings.Contains(yml, `{ from: "alice", to: "bob" }`) || strings.Contains(yml, "response") {
		t.Fatalf("the edge is rewritten:\n%s", yml)
	}
	if tm, err := team.LoadTeam(r.home); err != nil || !tm.Route("alice", "bob", 0).Allowed {
		t.Fatalf("the new edge loads: %v", err)
	}
}

func TestABundleThatFailsAfterItsUnlinkPutsTheEdgeBack(t *testing.T) {
	r := newRig(t)
	before := r.withOldEdge()
	p, id := r.propose("aaaa0006", "kind", "bundle", "agent", "3 changes",
		"i1.kind", "unlink", "i1.agent", "alice", "i1.to", "bob",
		"i2.kind", "link", "i2.agent", "alice", "i2.to", "bob",
		"i3.kind", "link", "i3.agent", "alice", "i3.to", "nobody")
	msg := r.decide(id, "approve", p.subject(), "d1")["error"].(map[string]any)["message"].(string)
	if !strings.Contains(msg, "step 3 of 3 (link alice) failed") || r.file("team.yaml") != before {
		t.Fatalf("%s\n%s", msg, r.file("team.yaml"))
	}
}

// approveBundle raises changes as one bundle from alice and approves it; the error reply, or nil.
func (r *rig) approveBundle(changes string) any {
	r.t.Helper()
	m := r.callAs("alice", "hr.propose", `{"kind":"bundle","args":{"name":"changes","changes":[`+changes+`]},"why":"w","request_id":"p1"}`)
	n, ok := m["result"].(map[string]any)["proposal"].(float64)
	if !ok {
		r.t.Fatalf("proposed: %v", m)
	}
	p, err := r.e.load(fmt.Sprintf("%08x", int64(n)))
	if err != nil {
		r.t.Fatal(err)
	}
	return r.decide(int64(n), "approve", p.subject(), "d1")["error"]
}

// An edge whose end is no agent (retired, or never one) can still be unlinked, from either end.
func TestAnEdgeToAnAgentThatIsGoneCanBeUnlinked(t *testing.T) {
	r := newRig(t)
	r.withEdges(`  - { from: "alice", to: "ghost" }` + "\n" + `  - { from: "ghost", to: "bob" }` + "\n")
	if err := r.approveBundle(`{"kind":"unlink","name":"alice","to":"ghost"},{"kind":"unlink","name":"ghost","to":"bob"}`); err != nil {
		t.Fatalf("%v", err)
	}
	if yml := r.file("team.yaml"); strings.Contains(yml, "ghost") {
		t.Fatalf("both edges are gone:\n%s", yml)
	}
	m := r.callAs("alice", "hr.propose", `{"kind":"unlink","args":{"name":"alice","to":"ghost"},"why":"w","request_id":"p2"}`)
	if msg, _ := m["error"].(map[string]any)["message"].(string); !strings.Contains(msg, "no edge exactly alice -> ghost") {
		t.Fatalf("an edge that is not there is still refused: %v", m)
	}
}

// The wildcard and founder edges are rewritten like any other: unlinked, then linked again with from and to alone.
func TestABundleRewritesAWildcardEdgeAndAFounderEdge(t *testing.T) {
	r := newRig(t)
	yml := strings.Replace(r.withEdges(`  - { from: "*", to: "bob", relation: peer }`+"\n"), `{ from: "human", to: "*" }`, `{ from: "human", to: "*", response: async }`, 1)
	if err := os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(yml), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := sealed.New(r.home).Seal(sealed.Team, yml); err != nil {
		t.Fatal(err)
	}
	if err := r.approveBundle(`{"kind":"unlink","name":"*","to":"bob"},{"kind":"link","name":"*","to":"bob"},` +
		`{"kind":"unlink","name":"human","to":"*"},{"kind":"link","name":"human","to":"*"}`); err != nil {
		t.Fatalf("%v", err)
	}
	got := r.file("team.yaml")
	if !strings.Contains(got, `{ from: "*", to: "bob" }`) || !strings.Contains(got, `{ from: "human", to: "*" }`) ||
		strings.Contains(got, "relation") || strings.Contains(got, "response") {
		t.Fatalf("both edges are rewritten:\n%s", got)
	}
	if tm, err := team.LoadTeam(r.home); err != nil || !tm.Route("alice", "bob", 0).Allowed {
		t.Fatalf("the new edges load: %v", err)
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
	if m := r.decide(id, "approve", p.subject(), "e1"); m["result"] == nil || !strings.Contains(m["result"].(map[string]any)["said"].(string), "when bob ends the turn") {
		t.Fatalf("held, not refused, while it runs: %v", m)
	}
	if r.state("aaaa0005") != "applying" || !strings.Contains(r.file("team.yaml"), "  bob: {}") {
		t.Fatalf("nothing changes while bob's turn runs: %s", r.state("aaaa0005"))
	}
	if m := r.decide(id, "approve", p.subject(), "e2"); gate(m) != "proposal_settled" {
		t.Fatalf("an applying proposal is not decided again: %v", m)
	}
	// a sandboxed agent's caches, with a read-only folder as Go's module cache keeps them
	cache := func(name string) string {
		ro := filepath.Join(r.home, "caches", name, "go-mod", "m@v1")
		os.MkdirAll(ro, 0o755)
		os.WriteFile(filepath.Join(ro, "go.mod"), nil, 0o444)
		os.Chmod(ro, 0o555)
		return filepath.Join(r.home, "caches", name)
	}
	bobs := cache("bob")
	idle, closed, started := false, []string{}, []string{}
	ports := HeldPorts{Alive: func(a string) bool { return r.live[a] }, Idle: func(string) bool { return idle },
		Close: func(a string) (bool, string) { closed = append(closed, a); r.live[a] = false; return true, "" },
		Start: func(a string) (bool, string) { started = append(started, a); return true, "" }}
	r.e.SweepHeld(ports) // mid-turn: not closed
	if len(closed) != 0 || r.state("aaaa0005") != "applying" {
		t.Fatalf("a turn is never cut: %v %s", closed, r.state("aaaa0005"))
	}
	idle = true
	r.e.SweepHeld(ports) // at its prompt: closed, applied on the next look
	if !reflect.DeepEqual(closed, []string{"bob"}) || r.state("aaaa0005") != "applying" {
		t.Fatalf("closed at its prompt: %v %s", closed, r.state("aaaa0005"))
	}
	r.e.SweepHeld(ports)
	if r.state("aaaa0005") != "applied" || !reflect.DeepEqual(started, []string{"robert"}) {
		t.Fatalf("applied, and the agent started under its new name: %s %v", r.state("aaaa0005"), started)
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
	if _, err := os.Stat(bobs); err == nil {
		t.Fatal("the old name's caches are left behind")
	}
	roberts := cache("robert")
	p2, id2 := r.propose("aaaa0006", "kind", "retire", "agent", "robert")
	res := r.decide(id2, "approve", p2.subject(), "e3")["result"].(map[string]any)
	if !strings.Contains(res["said"].(string), "robert is retired; its folder is at") {
		t.Fatalf("%v", res)
	}
	if _, err := os.Stat(filepath.Join(r.home, "agents", "robert")); err == nil {
		t.Fatal("archived")
	}
	if _, err := os.Stat(roberts); err == nil {
		t.Fatal("a retired agent's caches are left behind")
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
	p, id = r.propose("aaaa0009c", "kind", "global", "agent", "service-use", "set.value", "python")
	if res := r.decide(id, "approve", p.subject(), "f3c"); !strings.Contains(fmt.Sprint(res), "being removed") || !strings.Contains(r.file("config.yaml"), "daemon_impl: go") {
		t.Fatalf("the Python daemon is not chosen again: %v\n%s", res, r.file("config.yaml"))
	}
	p, id = r.propose("aaaa000a", "kind", "settings", "agent", "alice", "set.effort", "high")
	res := r.decide(id, "approve", p.subject(), "f4")
	if res["result"] == nil || !strings.Contains(r.file("agents", "alice", "agent.yaml"), "effort: high") {
		t.Fatalf("%v\n%s", res, r.file("agents", "alice", "agent.yaml"))
	}
}

func (auth) SessionsReadable() error { return nil }

func TestAContextFileHasNoSizeLimit(t *testing.T) {
	r := newRig(t)
	role := "# alice\n\n" + strings.Repeat("x", 3072) + "\n"
	ctx, _ := json.Marshal(role)
	p, id := r.propose("aaaa0010", "kind", "context", "agent", "alice", "set.ROLE.md", string(ctx))
	if res := r.decide(id, "approve", p.subject(), "g1"); r.file("agents", "alice", "ROLE.md") != role {
		t.Fatalf("a 3KB ROLE.md was not written: %v", res)
	}
}

func TestAgentSetTakesEveryFieldOfTheContractForTheFounder(t *testing.T) {
	r := newRig(t)
	for _, c := range []struct{ key, val, want string }{
		{"model", "opus", "model: opus"},
		{"effort", "high", "effort: high"},
		{"weight", "heavy", "weight: heavy"},
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
		"skip prompts":   `{"agent":"alice","values":{"skip_permissions":"on"},"request_id":"b4"}`,
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
	_, err := r.e.agentSetMethod(c, map[string]any{"agent": "alice", "values": map[string]any{"sandbox": "off"}})
	var re *rpc.Error
	if !errors.As(err, &re) || re.Data["gate"] != "role" {
		t.Fatalf("a device was not refused at the role gate: %v", err)
	}
	if r.file("agents", "alice", "agent.yaml") != before {
		t.Fatal("a refused device changed the agent")
	}
}

func errOf(t *testing.T, res map[string]any) map[string]any {
	t.Helper()
	e, _ := res["error"].(map[string]any)
	if e == nil {
		t.Fatalf("not refused: %v", res)
	}
	d, _ := e["data"].(map[string]any)
	return d
}

func TestAnAgentSetsTheOrdinaryKeysOfItselfAndNothingElse(t *testing.T) {
	r := newRig(t)
	set := func(agent string, values string, rid string) map[string]any {
		return r.callAs("alice", "agent.set", fmt.Sprintf(`{"agent":%q,"values":%s,"request_id":%q}`, agent, values, rid))
	}
	if res := set("alice", `{"effort":"high","context_k":"120"}`, "ag1"); res["result"] == nil {
		t.Fatalf("an agent's own ordinary keys: %v", res)
	}
	if !strings.Contains(r.file("agents", "alice", "agent.yaml"), "effort: high") {
		t.Fatalf("not written:\n%s", r.file("agents", "alice", "agent.yaml"))
	}
	bob := r.file("agents", "bob", "agent.yaml")
	if d := errOf(t, set("bob", `{"effort":"low"}`, "ag2")); d["code"] != "refused" || d["gate"] != "not_self" {
		t.Fatalf("another agent's settings: %v", d)
	}
	if r.file("agents", "bob", "agent.yaml") != bob {
		t.Fatal("a refused call changed another agent")
	}
	alice := r.file("agents", "alice", "agent.yaml")
	for i, key := range []string{"sandbox", "mode"} { // the founder's alone
		if d := errOf(t, set("alice", fmt.Sprintf(`{%q:"on"}`, key), fmt.Sprintf("ag-f%d", i))); d["code"] != "refused" {
			t.Errorf("%s: an agent set a founder-only key: %v", key, d)
		}
	}
	if r.file("agents", "alice", "agent.yaml") != alice {
		t.Fatalf("a founder-only key was written:\n%s", r.file("agents", "alice", "agent.yaml"))
	}
}

func TestAgentSetNamesWhatWentWrong(t *testing.T) {
	r := newRig(t)
	call := func(params string) map[string]any {
		return errOf(t, r.call("agent.set", params))
	}
	if d := call(`{"agent":"ghost","values":{"effort":"high"},"request_id":"n1"}`); d["code"] != "not_found" {
		t.Errorf("an agent that is not there: %v", d)
	}
	if d := call(`{"agent":"alice","values":{"effort":"extreme"},"request_id":"n2"}`); d["code"] != "refused" {
		t.Errorf("a bad value: %v", d)
	}
	if d := call(`{"agent":"alice","values":{"nope":"x"},"request_id":"n3"}`); d["code"] != "refused" {
		t.Errorf("an unknown key: %v", d)
	}
}

func TestARetriedAgentSetIsAnsweredAndWritesNothing(t *testing.T) {
	r := newRig(t)
	call := `{"agent":"alice","values":{"effort":"high"},"request_id":"re1"}`
	first, _ := r.call("agent.set", call)["result"].(map[string]any)
	if first["accepted"] != true || first["replayed"] != nil {
		t.Fatalf("first: %v", first)
	}
	path := filepath.Join(r.home, "agents", "alice", "agent.yaml")
	before, _ := os.Stat(path)
	// the file changes behind the retry: a rewrite would put "high" back
	if err := os.WriteFile(path, []byte(strings.Replace(r.file("agents", "alice", "agent.yaml"), "effort: high", "effort: low", 1)), 0o644); err != nil {
		t.Fatal(err)
	}
	changed, _ := os.Stat(path)
	again, _ := r.call("agent.set", call)["result"].(map[string]any)
	if again["replayed"] != true || again["accepted"] != true {
		t.Fatalf("retry: %v", again)
	}
	after, _ := os.Stat(path)
	if !after.ModTime().Equal(changed.ModTime()) || strings.Contains(r.file("agents", "alice", "agent.yaml"), "effort: high") {
		t.Fatalf("the retry rewrote the file (was %v)", before.ModTime())
	}
}

func TestWithdrawTakesBackAnUndecidedProposalForItsAuthorOrTheFounderAndNoOtherAgent(t *testing.T) {
	r := newRig(t)
	_, a := r.propose("aaaa0010", "kind", "link", "agent", "alice", "to", "bob")
	_, b := r.propose("aaaa0011", "kind", "link", "agent", "alice", "to", "bob")
	_, c := r.propose("aaaa0012", "kind", "link", "agent", "alice", "to", "bob")
	call := func(who string, id int64, rid string) map[string]any {
		params := fmt.Sprintf(`{"proposal":%d,"request_id":%q}`, id, rid)
		if who == "" {
			return r.call("hr.withdraw", params)
		}
		return r.callAs(who, "hr.withdraw", params)
	}
	if m := call("bob", a, "w1"); gate(m) != "role" || r.state("aaaa0010") != "proposed" {
		t.Fatalf("another agent withdrew it: %v %s", m, r.state("aaaa0010"))
	}
	res := call("alice", a, "w2")["result"].(map[string]any)
	if res["said"] != "proposal aaaa0010 withdrawn" || r.state("aaaa0010") != "withdrawn" {
		t.Fatalf("%v %s", res, r.state("aaaa0010"))
	}
	if p, _ := r.e.load("aaaa0010"); p.get("decided") == nil {
		t.Fatal("the withdrawal is not dated")
	}
	if again := call("alice", a, "w2")["result"].(map[string]any); again["replayed"] != true {
		t.Fatalf("a retry repeats nothing: %v", again)
	}
	// the founder takes back any, even one approved and not applied
	if m := call("", b, "w3"); m["result"] == nil || r.state("aaaa0011") != "withdrawn" {
		t.Fatalf("%v", m)
	}
	p, _ := r.e.load("aaaa0012")
	p.set("state", "approved")
	r.e.save(p)
	if m := call("", c, "w4"); m["result"] == nil || r.state("aaaa0012") != "withdrawn" {
		t.Fatalf("%v", m)
	}
}

func TestWithdrawOfASettledOrUnknownProposalIsRefusedWithTheProposalAsItIs(t *testing.T) {
	r := newRig(t)
	_, id := r.propose("aaaa0020", "kind", "link", "agent", "alice", "to", "bob")
	r.decide(id, "reject", "", "d1")
	m := r.callAs("alice", "hr.withdraw", fmt.Sprintf(`{"proposal":%d,"request_id":"w1"}`, id))
	e, _ := m["error"].(map[string]any)
	d, _ := e["data"].(map[string]any)
	if d["gate"] != "proposal_settled" || d["proposal"] == nil || r.state("aaaa0020") != "rejected" {
		t.Fatalf("%v", m)
	}
	if m := r.callAs("alice", "hr.withdraw", `{"proposal":999999,"request_id":"w2"}`); m["error"].(map[string]any)["data"].(map[string]any)["code"] != "not_found" {
		t.Fatalf("%v", m)
	}
	if m := r.callAs("alice", "hr.withdraw", `{"proposal":"x","request_id":"w3"}`); m["error"].(map[string]any)["data"].(map[string]any)["code"] != "bad_request" {
		t.Fatalf("%v", m)
	}
}

func TestAPairedDeviceDecidesAndAnAgentSessionIsRefused(t *testing.T) {
	r := newRig(t)
	_, id := r.propose("aaaa0003", "kind", "link", "agent", "alice", "to", "bob")
	refused := r.callAs("alice", "hr.decide", fmt.Sprintf(`{"proposal":%d,"decision":"reject","request_id":"g1"}`, id))
	if gate(refused) != "role" || r.state("aaaa0003") != "proposed" {
		t.Fatalf("an agent does not decide: %v %s", refused, r.state("aaaa0003"))
	}
	r.device = "dev-1"
	res := r.decide(id, "reject", "", "g2")["result"].(map[string]any)
	if res["said"] != "proposal aaaa0003 rejected" || r.state("aaaa0003") != "rejected" {
		t.Fatalf("a paired device decides: %v %s", res, r.state("aaaa0003"))
	}
}

func (r *rig) told(who string) []string {
	r.t.Helper()
	rows, err := r.db.Query("SELECT body FROM requests WHERE target = ? AND requester = ? AND notice >= 1 ORDER BY id", who, dbpkg.NoticeRequester)
	if err != nil {
		r.t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var b string
		rows.Scan(&b)
		out = append(out, b)
	}
	return out
}

func TestTheProposerIsToldHowEachDecisionWentInTheWordsHrPyUsed(t *testing.T) {
	r := newRig(t)
	_, id := r.propose("aaaa0011", "kind", "link", "agent", "alice", "to", "bob")
	r.call("hr.decide", fmt.Sprintf(`{"proposal":%d,"decision":"reject","note":"not now","request_id":"t1"}`, id))
	_, id = r.propose("aaaa0012", "kind", "link", "agent", "alice", "to", "bob")
	r.decide(id, "reject", "", "t2")
	p, id := r.propose("aaaa0013", "kind", "link", "agent", "alice", "to", "bob")
	r.decide(id, "approve", p.subject(), "t3")
	p, id = r.propose("aaaa0014", "kind", "bundle", "agent", "2 changes",
		"i1.kind", "add", "i1.agent", "dave", "i1.team", "eng",
		"i2.kind", "link", "i2.agent", "dave", "i2.to", "nobody")
	r.decide(id, "approve", p.subject(), "t4")
	got := r.told("alice")
	if len(got) != 4 || got[0] != "proposal aaaa0011 was rejected: not now" || got[1] != "proposal aaaa0012 was rejected." ||
		!strings.HasPrefix(got[2], "proposal aaaa0013 applied: alice may now ask bob.") ||
		!strings.HasPrefix(got[3], "proposal aaaa0014 was approved but could not be applied: step 2 of 2 (link dave) failed") {
		t.Fatalf("%q", got)
	}
}

func TestABatchOfDecisionsWaitsForTheProposersDigestAndNothingIsFromTheFounder(t *testing.T) {
	r := newRig(t)
	for i := 0; i < 5; i++ {
		p, id := r.propose(fmt.Sprintf("aaaa002%d", i), "kind", "link", "agent", "alice", "to", "bob")
		r.decide(id, ifThen(i%2 == 0, "approve", "reject"), p.subject(), fmt.Sprintf("b%d", i))
	}
	for _, who := range []string{"alice", "bob"} {
		if pending, err := dbpkg.PendingNotices(r.db, who); err != nil || len(pending) != 5 {
			t.Fatalf("%s: five outcomes wait for one digest: %d %v", who, len(pending), err)
		}
	}
	var fromFounder int
	r.db.QueryRow("SELECT COUNT(*) FROM requests WHERE target = 'alice' AND requester = 'human'").Scan(&fromFounder)
	if fromFounder != 0 {
		t.Fatalf("%d outcomes were said as the founder", fromFounder)
	}
}

func TestAnOutcomeReachesTheAgentsABundleNamesOnceEach(t *testing.T) {
	r := newRig(t)
	if _, err := team.NewRoster(r.home).Register("carol", team.RegisterOpts{Role: "r"}); err != nil {
		t.Fatal(err)
	}
	_, id := r.propose("aaaa0031", "kind", "bundle", "agent", "2 changes",
		"i1.kind", "link", "i1.agent", "carol", "i1.to", "bob", "i2.kind", "link", "i2.agent", "alice", "i2.to", "alice")
	r.decide(id, "reject", "", "n1")
	for who, want := range map[string]int{"alice": 1, "bob": 1, "carol": 1} {
		if got := len(r.told(who)); got != want {
			t.Errorf("%s was told %d times, want %d", who, got, want)
		}
	}
}

func ifThen(c bool, a, b string) string {
	if c {
		return a
	}
	return b
}

func TestEachOutcomeMessageNamesTheProposalItIsAbout(t *testing.T) {
	r := newRig(t)
	_, rejected := r.propose("aaaa0015", "kind", "link", "agent", "alice", "to", "bob")
	r.decide(rejected, "reject", "", "o1")
	p, applied := r.propose("aaaa0016", "kind", "link", "agent", "alice", "to", "bob")
	r.decide(applied, "approve", p.subject(), "o2")
	rows, err := r.db.Query("SELECT json_extract(e.payload, '$.proposal') FROM requests q LEFT JOIN events e ON e.dedup_key = 'hr_outcome:' || q.id " +
		"WHERE q.target = 'alice' AND q.notice >= 1 ORDER BY q.id")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var got []int64
	for rows.Next() {
		var n sql.NullInt64
		rows.Scan(&n)
		got = append(got, n.Int64)
	}
	if fmt.Sprint(got) != fmt.Sprint([]int64{rejected, applied}) {
		t.Fatalf("each told message links its proposal %d and %d: %v", rejected, applied, got)
	}
}

func TestACacheThatCannotBeRemovedDoesNotUndoARetire(t *testing.T) {
	r := newRig(t)
	caches := filepath.Join(r.home, "caches")
	os.MkdirAll(filepath.Join(caches, "bob", "go-build"), 0o755)
	os.Chmod(caches, 0o555) // bob's folder cannot leave it
	defer os.Chmod(caches, 0o755)
	p, id := r.propose("aaaa0009", "kind", "retire", "agent", "bob")
	res := r.decide(id, "approve", p.subject(), "g1")["result"].(map[string]any)
	if !strings.Contains(res["said"].(string), "bob is retired") || strings.Contains(r.file("team.yaml"), "bob:") {
		t.Fatalf("the retire stopped half done: %v", res)
	}
}

func TestTeamSealPlansWritesNothingSealsOnTheGoAheadAndRefusesAnAgent(t *testing.T) {
	r := newRig(t)
	res := func(m map[string]any) map[string]any {
		t.Helper()
		out, _ := m["result"].(map[string]any)
		if out == nil {
			t.Fatalf("%v", m)
		}
		return out
	}
	yaml := r.file("agents", "alice", "agent.yaml")
	if err := os.WriteFile(filepath.Join(r.home, "agents", "alice", "agent.yaml"), []byte(yaml+"# edited behind the seal\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	events := func() (n int) {
		t.Helper()
		if err := r.db.QueryRow("SELECT COUNT(*) FROM events WHERE kind != 'founder_key'").Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	before := events()
	plan := res(r.call("team.seal", `{"dry_run":true,"request_id":"s1"}`))
	if events() != before {
		t.Fatal("a dry run left an event")
	}
	text, _ := plan["plan"].(string)
	if plan["ok"] != true || !strings.Contains(text, "agent alice (it did not match its seal) as ") || !strings.Contains(text, "file(s) would be sealed") {
		t.Fatalf("%v", plan)
	}
	again := res(r.call("team.seal", `{"dry_run":true,"request_id":"s2"}`))
	if again["plan"] != plan["plan"] {
		t.Fatalf("a dry run changed something: %v", again)
	}
	done := res(r.call("team.seal", `{"request_id":"s3"}`))
	said, _ := done["said"].(string)
	if done["ok"] != true || !strings.Contains(said, "sealed agent alice (it did not match its seal)") || !strings.HasSuffix(said, "as they are now") {
		t.Fatalf("%v", done)
	}
	none := res(r.call("team.seal", `{"dry_run":true,"request_id":"s4"}`))
	if none["plan"] != "nothing to seal: every file already matches its seal" {
		t.Fatalf("%v", none)
	}
	bad, _ := r.call("team.seal", `{"dry_run":"yes","request_id":"s5"}`)["error"].(map[string]any)
	if bad == nil {
		t.Fatal("dry_run must be a boolean")
	}
	refused, _ := r.callAs("alice", "team.seal", `{"request_id":"s6"}`)["error"].(map[string]any)
	if refused == nil {
		t.Fatal("an agent sealed the team")
	}
}

func TestProposeWritesTheProposalFileAndRefusesWhatHrPyRefused(t *testing.T) {
	r := newRig(t)
	call := func(rid, params string) map[string]any {
		return r.callAs("alice", "hr.propose", fmt.Sprintf(`{%s,"request_id":%q}`, params, rid))
	}
	res := call("p1", `"kind":"retire","args":{"name":"bob"},"why":"idle"`)["result"].(map[string]any)
	n, ok := res["proposal"].(float64)
	if !ok || !strings.HasPrefix(res["said"].(string), "proposal ") {
		t.Fatalf("%v", res)
	}
	p, err := r.e.load(fmt.Sprintf("%08x", int64(n)))
	if err != nil || p == nil || p.str("kind") != "retire" || p.str("agent") != "bob" || p.str("requested_by") != "alice" ||
		p.str("state") != "proposed" || p.str("why") != "idle" {
		t.Fatalf("%v %+v", err, p)
	}
	ctx := call("p2", `"kind":"context","args":{"name":"bob","sets":{"ROLE.md":"new role"}},"why":"w"`)["result"].(map[string]any)
	cp, _ := r.e.load(fmt.Sprintf("%08x", int64(ctx["proposal"].(float64))))
	if cp == nil || cp.str("set.ROLE.md") != `"new role"` {
		t.Fatalf("a context text is kept as JSON on one line: %+v", cp)
	}
	for _, c := range []struct{ params, want string }{
		{`"kind":"add","args":{"name":"alice"},"why":"w"`, "alice is already an agent"},
		{`"kind":"retire","args":{"name":"ghost"},"why":"w"`, "ghost is not an agent"},
		{`"kind":"add","args":{"name":"carol"},"why":"w"`, "carol needs a repo"},
		{`"kind":"retire","args":{"name":"bob"},"why":" "`, "say why"},
		{`"kind":"link","args":{"name":"alice","to":"alice"},"why":"w"`, "an agent cannot ask itself"},
		{`"kind":"context","args":{"name":"bob","sets":{"x.md":"t"}},"why":"w"`, "only ROLE.md and base.md"},
	} {
		m := call("e"+c.want[:3], c.params)
		msg, _ := m["error"].(map[string]any)["message"].(string)
		if !strings.Contains(msg, c.want) {
			t.Errorf("%s: %v", c.want, m)
		}
	}
}

// gitDir is a fresh folder that reads as a git checkout.
func gitDir(t *testing.T) string {
	t.Helper()
	d := t.TempDir()
	if err := os.Mkdir(filepath.Join(d, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	return d
}

func TestProposeRenameTeamAndBundleAreDraftedAsHrPyDraftsThem(t *testing.T) {
	r := newRig(t)
	rr, ss := gitDir(t), gitDir(t)
	paths := strings.NewReplacer(`"/r"`, fmt.Sprintf("%q", rr), `"/s"`, fmt.Sprintf("%q", ss))
	call := func(rid, params string) map[string]any {
		return r.callAs("alice", "hr.propose", fmt.Sprintf(`{%s,"request_id":%q}`, paths.Replace(params), rid))
	}
	loaded := func(res map[string]any) *Prop {
		t.Helper()
		out, _ := res["result"].(map[string]any)
		n, ok := out["proposal"].(float64)
		if !ok {
			t.Fatalf("not raised: %v", res)
		}
		p, err := r.e.load(fmt.Sprintf("%08x", int64(n)))
		if err != nil || p == nil {
			t.Fatalf("%v", err)
		}
		return p
	}
	rn := loaded(call("r1", `"kind":"rename","args":{"name":"bob","to":"robert"},"why":"w"`))
	if rn.kind() != "rename" || rn.str("agent") != "bob" || rn.str("to") != "robert" {
		t.Fatalf("%+v", rn)
	}
	tm := loaded(call("t1", `"kind":"team","args":{"name":"zed","repo":"/r","members":["sea","zed.air"],"repos":{"zed.sea":"/s"}},"why":"w"`))
	if tm.str("members") != "zed.sea,zed.air" || tm.str("repo.zed.sea") != ss || tm.str("repo.zed.air") != rr {
		t.Fatalf("%+v", tm)
	}
	b := loaded(call("b1", `"kind":"bundle","args":{"name":"2 changes","changes":[{"kind":"rename","name":"carol","to":"cy"},{"kind":"add","name":"carol","repo":"/r"}]},"why":"w"`))
	if b.kind() != "bundle" || b.str("agent") != "2 changes" || len(b.items()) != 2 {
		t.Fatalf("%+v", b)
	}
	// ordered: the add comes before the rename in the apply order, so carol exists when the rename is judged
	if it := ordered(b.items()); it[0].kind() != "add" || it[1].kind() != "rename" {
		t.Fatalf("%v %v", it[0].kind(), it[1].kind())
	}
	for _, c := range []struct{ params, want string }{
		{`"kind":"rename","args":{"name":"bob"},"why":"w"`, "a rename needs a new name"},
		{`"kind":"rename","args":{"name":"bob","to":"alice"},"why":"w"`, "alice is already an agent"},
		{`"kind":"team","args":{"name":"zed","repo":"/r"},"why":"w"`, "a team needs at least one member"},
		{`"kind":"team","args":{"name":"zed","members":["sea"]},"why":"w"`, "needs --repo"},
		{`"kind":"team","args":{"name":"alice","repo":"/r","members":["sea"]},"why":"w"`, "alice already an agent"},
		{`"kind":"bundle","args":{"name":"0 changes","changes":[]},"why":"w"`, "needs at least one change"},
		{`"kind":"bundle","args":{"name":"1 changes","changes":[{"kind":"retire","name":"ghost"}]},"why":"w"`, "change 1 (retire ghost): ghost is not an agent"},
		{`"kind":"bundle","args":{"name":"1 changes","changes":[{"kind":"retire","name":"bob","bogus":1}]},"why":"w"`, "not an argument: bogus"},
	} {
		m := call("e"+c.want[:5], c.params)
		msg, _ := m["error"].(map[string]any)["message"].(string)
		if !strings.Contains(msg, c.want) {
			t.Errorf("%s: %v", c.want, m)
		}
	}
}

func TestAPairedDeviceSetsAnotherAgentsModelAndEffort(t *testing.T) {
	r := newRig(t)
	r.device = "dev-1"
	res := r.call("agent.set", `{"agent":"alice","values":{"model":"opus","effort":"high"},"request_id":"dev-set"}`)
	if got, _ := res["result"].(map[string]any); got["accepted"] != true {
		t.Fatalf("a paired device set model and effort: %v", res)
	}
	if all := r.file("agents", "alice", "agent.yaml") + r.file("team.yaml"); !strings.Contains(all, "opus") || !strings.Contains(all, "effort: high") {
		t.Errorf("not written:\n%s", all)
	}
}

// The founder's own hr add is applied at once, its approval on record as any other's, and hr.list shows it.
// The repo is checked again when a proposal is applied: one raised before the check (a relative path) or whose checkout is gone since is
// refused then, with nothing registered.
func TestAnApproveChecksTheRepoAgain(t *testing.T) {
	r := newRig(t)
	gone := gitDir(t)
	os.RemoveAll(gone)
	for i, c := range []struct{ kind, repo, want string }{
		{"add", "orcapipe/handline", "repo orcapipe/handline is not an absolute path"},
		{"add", gone, "is not a folder"},
		{"team", gone, "is not a folder"},
	} {
		id := fmt.Sprintf("aaab000%d", i)
		fields := []string{"kind", c.kind, "agent", "carol", "repo", c.repo}
		if c.kind == "team" {
			fields = append(fields, "members", "carol.a", "repo.carol.a", gitDir(t))
		}
		p, n := r.propose(id, fields...)
		res := r.decide(n, "approve", p.subject(), "d"+id)
		msg, _ := res["error"].(map[string]any)["message"].(string)
		if !strings.Contains(msg, c.want) {
			t.Fatalf("%s %s: %v", c.kind, c.repo, res)
		}
		if _, err := os.Stat(filepath.Join(r.home, "agents", "carol")); err == nil {
			t.Fatalf("%s %s: carol is registered", c.kind, c.repo)
		}
	}
}

func TestTheFoundersOwnAddIsAppliedAtOnce(t *testing.T) {
	r := newRig(t)
	repo := t.TempDir()
	os.Mkdir(filepath.Join(repo, ".git"), 0o755)
	m := r.call("hr.propose", fmt.Sprintf(`{"kind":"add","args":{"name":"carol","team":"eng","repo":%q},"why":"we need her","request_id":"f1"}`, repo))
	res, ok := m["result"].(map[string]any)
	if !ok {
		t.Fatalf("%v", m)
	}
	id := fmt.Sprintf("%08x", int64(res["proposal"].(float64)))
	if said := res["said"].(string); !strings.HasPrefix(said, "proposal "+id+" applied: carol is an agent, team eng.") {
		t.Fatalf("%q", said)
	}
	if st := r.state(id); st != "applied" {
		t.Fatalf("state %s", st)
	}
	if got := r.e.decisionOf(id); got == nil || got["call"] != "approved" {
		t.Fatalf("the approval is on record: %v", got)
	}
	if _, err := os.Stat(filepath.Join(r.home, "agents", "carol", "agent.yaml")); err != nil {
		t.Fatal("registered")
	}
	if v := r.e.reader().HRView(id); v == nil || v["state"] != "approved" || v["requested_by"] != "human" || v["note"] != nil {
		t.Fatalf("hr.list shows it, with no note: %v", v)
	}
}

// sweepEnded sweeps once after bob's turn ended and his session closed, starting agents with start.
func (r *rig) sweepEnded(start func(string) (bool, string)) {
	r.live["bob"] = false
	r.e.SweepHeld(HeldPorts{Alive: func(a string) bool { return r.live[a] }, Idle: func(string) bool { return true },
		Close: func(string) (bool, string) { return true, "" }, Start: start})
}

// A held change is checked against the founder's approval again before it is applied: a draft changed while it waited is not applied.
func TestAHeldChangeWhoseDraftChangedWhileItWaitedIsNotApplied(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa000a", "kind", "rename", "agent", "bob", "to", "robert")
	r.live["bob"] = true
	if m := r.decide(id, "approve", p.subject(), "h1"); m["result"] == nil || r.state("aaaa000a") != "applying" {
		t.Fatalf("held: %v %s", m, r.state("aaaa000a"))
	}
	held, err := r.e.load("aaaa000a")
	if err != nil {
		t.Fatal(err)
	}
	held.set("to", "mallory")
	if err := r.e.save(held); err != nil {
		t.Fatal(err)
	}
	r.sweepEnded(func(string) (bool, string) { return true, "" })
	if st := r.state("aaaa000a"); st != "approved" || !strings.Contains(r.file("hr", "aaaa000a.yaml"), "was changed after the founder approved it") {
		t.Fatalf("apply_failed, with why: %s\n%s", st, r.file("hr", "aaaa000a.yaml"))
	}
	if yml := r.file("team.yaml"); !strings.Contains(yml, "  bob: {}") || strings.Contains(yml, "mallory") {
		t.Fatalf("nothing renamed:\n%s", yml)
	}
}

// followUp is hr.list's follow_up of proposal pid (nil when none).
func (r *rig) followUp(pid string) map[string]any {
	v := r.e.reader().HRView(pid)
	f, _ := v["follow_up"].(map[string]any)
	return f
}

// A renamed agent whose start is held for machine load: the change is applied (approved on the wire), the follow-up waits, and the
// sweep starts the agent by itself once the load allows, with nothing left on the proposal.
func TestAHeldRenameWhoseStartIsHeldIsAppliedAndStartsByItself(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa000b", "kind", "rename", "agent", "bob", "to", "robert")
	r.live["bob"] = true
	r.decide(id, "approve", p.subject(), "h2")
	r.sweepEnded(func(string) (bool, string) { return false, "held: machine load - load 3.09 per core, limit 1.50" })
	if v := r.e.reader().HRView("aaaa000b"); v["state"] != "approved" || v["error"] != nil {
		t.Fatalf("applied, no error: %v", v)
	}
	f := r.followUp("aaaa000b")
	if f["agent"] != "robert" || f["state"] != "waiting" || !strings.Contains(f["text"].(string), "machine load") {
		t.Fatalf("follow-up waits: %v", f)
	}
	if !strings.Contains(r.file("team.yaml"), "  robert: {}") {
		t.Fatal("the rename itself stands")
	}
	still := func(string) (bool, string) { return false, "held: machine load - load 3.00 per core, limit 1.50" }
	r.sweepEnded(still)
	if r.followUp("aaaa000b")["state"] != "waiting" {
		t.Fatal("still waiting while held")
	}
	var started []string
	r.sweepEnded(func(a string) (bool, string) { started = append(started, a); return true, "" })
	if !slices.Equal(started, []string{"robert"}) || r.followUp("aaaa000b") != nil {
		t.Fatalf("started %v, follow-up %v", started, r.followUp("aaaa000b"))
	}
}

// A start that fails for another reason is a failed follow-up, not an apply failure, and the sweep does not retry it.
func TestAHeldRenameWhoseStartFailsIsAppliedWithAFailedFollowUp(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa000c", "kind", "rename", "agent", "bob", "to", "robert")
	r.live["bob"] = true
	r.decide(id, "approve", p.subject(), "h3")
	r.sweepEnded(func(string) (bool, string) { return false, "no account free" })
	if v := r.e.reader().HRView("aaaa000c"); v["state"] != "approved" {
		t.Fatalf("applied: %v", v)
	}
	if f := r.followUp("aaaa000c"); f["state"] != "failed" || f["text"] != "no account free" {
		t.Fatalf("failed follow-up: %v", f)
	}
	calls := 0
	r.sweepEnded(func(string) (bool, string) { calls++; return true, "" })
	if calls != 0 {
		t.Fatal("a failed start is not retried by the sweep")
	}
}

// A failed follow-up ends when the agent is running, as after the founder's Start now; the sweep never starts it itself.
func TestAFailedFollowUpEndsWhenTheAgentIsRunning(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa000f", "kind", "rename", "agent", "bob", "to", "robert")
	r.live["bob"] = true
	r.decide(id, "approve", p.subject(), "h6")
	r.sweepEnded(func(string) (bool, string) { return false, "no account free" })
	if r.followUp("aaaa000f")["state"] != "failed" {
		t.Fatal("failed first")
	}
	r.live["robert"] = true
	started := 0
	r.sweepEnded(func(string) (bool, string) { started++; return true, "" })
	if r.followUp("aaaa000f") != nil || started != 0 {
		t.Fatalf("follow-up gone, no start by the sweep: %v %d", r.followUp("aaaa000f"), started)
	}
}

// A rename whose agent starts at once leaves no follow-up.
func TestAHeldRenameThatStartsLeavesNoFollowUp(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa000d", "kind", "rename", "agent", "bob", "to", "robert")
	r.live["bob"] = true
	r.decide(id, "approve", p.subject(), "h4")
	r.sweepEnded(func(string) (bool, string) { return true, "" })
	if v := r.e.reader().HRView("aaaa000d"); v["state"] != "approved" || r.followUp("aaaa000d") != nil {
		t.Fatalf("applied, no follow-up: %v", v)
	}
}

// A founder's proposal left waiting (raised before their own were applied at once) is listed and approved like any other.
func TestAFoundersProposalLeftWaitingIsListedAndApprovable(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa0009", "kind", "retire", "agent", "bob")
	p.set("requested_by", "human")
	if err := r.e.save(p); err != nil {
		t.Fatal(err)
	}
	if v := r.e.reader().HRView(p.id()); v == nil || v["state"] != "open" {
		t.Fatalf("listed as waiting: %v", v)
	}
	if m := r.decide(id, "approve", p.subject(), "w1"); m["error"] != nil {
		t.Fatalf("%v", m)
	}
	if st := r.state(p.id()); st != "applied" {
		t.Fatalf("state %s", st)
	}
}

// A relative --repo (and a member's SUFFIX=REPO) is taken from the proposer's own repo and kept absolute; a path that is not a git checkout,
// or a relative one from a proposer with no repo (the founder), is refused before anything is written.
func TestAProposedRepoIsKeptAbsoluteAndMustBeAGitCheckout(t *testing.T) {
	r := newRig(t)
	base := gitDir(t)
	for _, sub := range []string{"app", "lib"} {
		if err := os.MkdirAll(filepath.Join(base, sub, ".git"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	os.Mkdir(filepath.Join(base, "plain"), 0o755)
	if _, err := team.NewRoster(r.home).Register("alice", team.RegisterOpts{Role: "r", Repo: base}); err != nil {
		t.Fatal(err)
	}
	propose := func(as, rid, params string) map[string]any {
		if as == "" {
			return r.call("hr.propose", fmt.Sprintf(`{%s,"request_id":%q}`, params, rid))
		}
		return r.callAs(as, "hr.propose", fmt.Sprintf(`{%s,"request_id":%q}`, params, rid))
	}
	stored := func(res map[string]any) *Prop {
		t.Helper()
		out, ok := res["result"].(map[string]any)
		if !ok {
			t.Fatalf("not raised: %v", res)
		}
		p, _ := r.e.load(fmt.Sprintf("%08x", int64(out["proposal"].(float64))))
		return p
	}
	if p := stored(propose("alice", "a1", `"kind":"add","args":{"name":"carol","repo":"app"},"why":"w"`)); p.str("repo") != filepath.Join(base, "app") {
		t.Fatalf("the relative repo is alice's own repo's app: %q", p.str("repo"))
	}
	tm := stored(propose("alice", "t1", `"kind":"team","args":{"name":"zed","repo":"app","members":["sea"],"repos":{"zed.sea":"lib"}},"why":"w"`))
	if tm.str("repo") != filepath.Join(base, "app") || tm.str("repo.zed.sea") != filepath.Join(base, "lib") {
		t.Fatalf("%q %q", tm.str("repo"), tm.str("repo.zed.sea"))
	}
	for _, c := range []struct{ as, params, want string }{
		{"alice", `"kind":"add","args":{"name":"dan","repo":"plain"},"why":"w"`, "repo plain is not a git checkout (" + filepath.Join(base, "plain") + ")"},
		{"alice", `"kind":"add","args":{"name":"dan","repo":"orcapipe/handline"},"why":"w"`, "repo orcapipe/handline is not a folder"},
		{"alice", `"kind":"team","args":{"name":"zed","repo":"app","members":["sea"],"repos":{"zed.sea":"plain"}},"why":"w"`, "repo plain is not a git checkout"},
		{"", `"kind":"add","args":{"name":"dan","repo":"app"},"why":"w"`, "repo app is a relative path and human has no repo to take it from"},
	} {
		m := propose(c.as, "e"+c.want[:12], c.params)
		msg, _ := m["error"].(map[string]any)["message"].(string)
		if !strings.HasPrefix(msg, c.want) {
			t.Errorf("%s: %v", c.want, m)
		}
	}
	if _, err := os.Stat(filepath.Join(r.home, "agents", "dan")); err == nil {
		t.Fatal("a refused add registers nothing")
	}
}

// hr.apply_now on a proposal waiting for a busy turn: the sweep interrupts that turn, and once the session is idle closes it and applies
// the change; the view shows since when it waited. Only an applying proposal is accepted.
func TestApplyNowInterruptsTheBusyTurnAndAppliesTheChange(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa000e", "kind", "retire", "agent", "bob")
	if m := r.call("hr.apply_now", fmt.Sprintf(`{"proposal":%d,"request_id":"an0"}`, id)); gate(m) != "proposal_settled" {
		t.Fatalf("an open proposal is refused: %v", m)
	}
	r.live["bob"] = true
	r.decide(id, "approve", p.subject(), "h5")
	if v := r.e.reader().HRView("aaaa000e"); v["state"] != "applying" || v["waiting_since"] == nil {
		t.Fatalf("waits, with since when: %v", v)
	}
	busy := true
	interrupted := 0
	ports := HeldPorts{Alive: func(a string) bool { return r.live[a] }, Idle: func(string) bool { return !busy },
		Interrupt: func(string) (bool, string) { interrupted++; busy = false; return true, "" },
		Close:     func(string) (bool, string) { r.live["bob"] = false; return true, "" },
		Start:     func(string) (bool, string) { return true, "" }}
	r.e.SweepHeld(ports)
	if interrupted != 0 || r.state("aaaa000e") != "applying" {
		t.Fatalf("without apply_now the busy turn is left alone: %d %s", interrupted, r.state("aaaa000e"))
	}
	if m := r.call("hr.apply_now", fmt.Sprintf(`{"proposal":%d,"request_id":"an1"}`, id)); m["result"] == nil {
		t.Fatalf("accepted: %v", m)
	}
	for range 3 { // interrupt, close, apply: one step a tick
		r.e.SweepHeld(ports)
	}
	if interrupted != 1 || r.state("aaaa000e") != "applied" {
		t.Fatalf("interrupted once, then applied: %d %s", interrupted, r.state("aaaa000e"))
	}
	if v := r.e.reader().HRView("aaaa000e"); v["waiting_since"] != nil {
		t.Fatalf("no wait left: %v", v)
	}
}

// A turn that is slow to end after the Esc is not pressed again on the next tick, only after the pause.
func TestApplyNowPressesEscOnceThenWaits(t *testing.T) {
	r := newRig(t)
	p, id := r.propose("aaaa0010", "kind", "retire", "agent", "bob")
	r.live["bob"] = true
	r.decide(id, "approve", p.subject(), "h7")
	r.call("hr.apply_now", fmt.Sprintf(`{"proposal":%d,"request_id":"an2"}`, id))
	interrupted := 0
	ports := HeldPorts{Alive: func(a string) bool { return r.live[a] }, Idle: func(string) bool { return false },
		Interrupt: func(string) (bool, string) { interrupted++; return true, "" },
		Close:     func(string) (bool, string) { return true, "" }, Start: func(string) (bool, string) { return true, "" }}
	for range 3 {
		r.e.SweepHeld(ports)
	}
	if interrupted != 1 {
		t.Fatalf("one Esc while the turn is slow to end: %d", interrupted)
	}
	defer func(was time.Duration) { interruptPause = was }(interruptPause)
	interruptPause = 0
	r.e.SweepHeld(ports)
	if interrupted != 2 {
		t.Fatalf("pressed again after the pause: %d", interrupted)
	}
}
