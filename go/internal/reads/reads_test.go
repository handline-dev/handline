package reads

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
)

func testEnv(t *testing.T) *Env {
	t.Helper()
	home := t.TempDir()
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return &Env{DB: db, Home: home, Started: time.Now()}
}

func addAgent(t *testing.T, e *Env, name, role string) {
	t.Helper()
	dir := filepath.Join(e.Home, "agents", name)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(dir, "agent.yaml"), nil, 0o644)
	if role != "" {
		os.WriteFile(filepath.Join(dir, "ROLE.md"), []byte(role), 0o644)
	}
}

func TestAnAgentWithNoStoredViewIsListedWithItsRoleAndTheNoTerminalState(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "worker", "# worker\n\n  Does   the work.\nSecond.\n")
	addAgent(t, e, "manager", "")
	if _, err := e.DB.Exec(`INSERT INTO agent_view(agent, view, updated) VALUES('manager', '{"name":"manager","state":"idle"}', 0)`); err != nil {
		t.Fatal(err)
	}
	got, err := e.agentsList(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct{ Agents []map[string]any }
	json.Unmarshal(b, &out)
	if len(out.Agents) != 2 || out.Agents[0]["state"] != "idle" || out.Agents[1]["name"] != "worker" ||
		out.Agents[1]["state"] != "no_terminal" || out.Agents[1]["role"] != "Does   the work." {
		t.Fatalf("%s", b)
	}
}

func TestTheWordForNoStoredViewIsOneOfTheSpecs(t *testing.T) {
	raw, err := os.ReadFile("../../../contract/protocol.json")
	if err != nil {
		t.Fatal(err)
	}
	var spec struct {
		Words struct {
			Words map[string]any `json:"words"`
		} `json:"x-state-words"`
	}
	if err := json.Unmarshal(raw, &spec); err != nil {
		t.Fatal(err)
	}
	if _, ok := spec.Words.Words[noView]; !ok {
		t.Fatalf("%q is not in x-state-words", noView)
	}
}

func TestAnAgentThatIsNotRegisteredIsNotFound(t *testing.T) {
	e := testEnv(t)
	if _, err := e.named(map[string]any{"agent": "ghost"}); err == nil || err.Error() == "" {
		t.Fatal("a missing agent was found")
	}
	if _, err := e.named(map[string]any{}); err == nil {
		t.Fatal("no agent param was accepted")
	}
}

func TestTheTaskLadderMatchesTheBoard(t *testing.T) {
	b := &board{begun: map[int64]bool{3: true}, sent: map[int64]bool{2: true}, children: map[int64][]int64{5: {2}},
		current: map[string]int64{"w": 9}, held: map[int64]row{9: {"done_evidence": "x"}}}
	mk := func(id int64, owner string, extra row) row {
		r := row{"id": id, "owner": owner}
		for k, v := range extra {
			r[k] = v
		}
		return r
	}
	for _, c := range []struct {
		t    row
		want string
	}{
		{mk(1, "w", nil), "draft"},
		{mk(1, "w", row{"queued_by": "m"}), "queued"},
		{mk(2, "w", nil), "doing"},                       // a request is out and its owner's held task is over
		{mk(3, "human", nil), "doing"},                   // begun on a founder's message
		{mk(5, "w", nil), "doing"},                       // an open child has one out
		{mk(2, "w", row{"done_evidence": "ok"}), "done"}, // written states win
		{mk(2, "w", row{"discarded_by": "m", "done_evidence": "ok"}), "discarded"},
	} {
		if got := b.state(c.t); got != c.want {
			t.Errorf("%v: %s, want %s", c.t, got, c.want)
		}
	}
	b.held[9] = row{}
	if got := b.state(mk(2, "w", nil)); got != "queued" {
		t.Errorf("a task its owner does not hold reads %s, not queued", got)
	}
}

func TestABoardReadsATaskStartedAsItsOwnersOwnAsBegun(t *testing.T) {
	e := testEnv(t)
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'self', 'w', 'w', 1), (2, 'founder', 'w', 'w', 1), (3, 'plain', 'w', 'w', 1)",
		`INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'w', 'task_self_start', '{"task_id": 1}'), (1, 'w', 'task_begin', '{"task_id": 2}')`,
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	b, err := e.loadBoard(nil)
	if err != nil {
		t.Fatal(err)
	}
	if !b.begun[1] || !b.begun[2] || b.begun[3] {
		t.Fatalf("begun is a task_self_start or a task_begin, nothing else: %v", b.begun)
	}
	if got := b.state(row{"id": int64(1), "owner": "w", "queued_by": "w"}); got != "doing" {
		t.Errorf("a self-started task reads %s, not doing", got)
	}
}

func TestDurationsAndAges(t *testing.T) {
	for in, want := range map[int64]string{7200: "2h", 5400: "90m", 86400: "1d", 61: "61s"} {
		if got := duration(in); got != want {
			t.Errorf("duration(%d) = %s, want %s", in, got, want)
		}
	}
	for in, want := range map[int64]string{5: "5s", 130: "2m", 7200: "2h", 86400 + 3600: "1d1h"} {
		if got := ago(in); got != want {
			t.Errorf("ago(%d) = %s, want %s", in, got, want)
		}
	}
}

func TestChatRefsAreTheAgentsNewestRequestsAndLeaveOutWhatNeverReached(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "alice", "")
	for _, q := range []string{
		"INSERT INTO requests(id, ts, requester, target, body, state) VALUES(1, 1, 'human', 'alice', '  ', 'queued')",
		"INSERT INTO requests(id, ts, requester, target, body, state) VALUES(2, 2, 'alice', 'bob', 'first line' || char(10) || 'second', 'queued')",
		"INSERT INTO requests(id, ts, requester, target, body, state) VALUES(3, 3, 'human', 'alice', 'taken back', 'withdrawn')",
		"INSERT INTO requests(id, ts, requester, target, body, state) VALUES(4, 4, 'bob', 'carol', 'not alice''s', 'queued')",
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	got, err := e.chatRefs(nil, map[string]any{"agent": "alice"})
	if err != nil {
		t.Fatal(err)
	}
	reqs := got.(map[string]any)["requests"].([]any)
	first := reqs[0].(map[string]any)
	if len(reqs) != 2 || first["request"] != int64(2) || first["line"] != "first line" || first["from"] != "alice" || first["to"] != "bob" {
		t.Fatalf("%v", reqs)
	}
	if _, err := e.chatRefs(nil, map[string]any{"agent": "nobody"}); err == nil {
		t.Fatal("an agent that is not registered is not found")
	}
}

func TestAStructuredAgentSaysSoInItsViewAndATerminalAgentDoesNot(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "alice", "")
	addAgent(t, e, "bob", "")
	if _, err := e.DB.Exec(`INSERT INTO agent_view(agent, view, updated) VALUES('alice', '{"name":"alice","state":"idle","n":12345678901234567}', 0)`); err != nil {
		t.Fatal(err)
	}
	e.Structured = func(agent string) bool { return agent == "alice" }
	got, err := e.agentsList(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct{ Agents []map[string]any }
	json.Unmarshal(b, &out)
	if out.Agents[0]["structured"] != true || out.Agents[1]["structured"] != nil {
		t.Fatalf("%s", b)
	}
	if !strings.Contains(string(b), "12345678901234567") {
		t.Fatalf("the stored view's numbers are kept exactly: %s", b)
	}
}

func TestSessionOpenOfAStructuredAgentIsItsRunningProcessNotATerminal(t *testing.T) {
	e := testEnv(t)
	for _, n := range []string{"live", "parked", "term"} {
		addAgent(t, e, n, "")
		var terminal any
		if n == "term" {
			terminal = "t1"
		}
		if _, err := e.DB.Exec(`INSERT INTO agent_state(agent, state, updated, terminal) VALUES(?, 'idle', 0, ?)`, n, terminal); err != nil {
			t.Fatal(err)
		}
	}
	e.Structured = func(agent string) bool { return agent != "term" }
	e.SessionOpen = func(agent string) bool { return agent == "live" }
	for agent, want := range map[string]bool{"live": true, "parked": false, "term": true} {
		got, err := e.agentGet(nil, map[string]any{"agent": agent})
		if err != nil {
			t.Fatal(err)
		}
		m := got.(map[string]any)
		if m["session_open"] != want {
			t.Fatalf("%s: session_open %v, want %v", agent, m["session_open"], want)
		}
		if holders := m["input_holders"].([]any); (agent != "term") != (len(holders) == 0) {
			t.Fatalf("%s: input_holders %v", agent, holders)
		}
		if (agent == "term") != (m["terminal"] != nil) {
			t.Fatalf("%s: terminal %v", agent, m["terminal"])
		}
	}
}

func TestAgentStripReadsTheStoredViewAndTheContextHook(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "alice", "")
	until := time.Now().Add(90 * time.Second).UTC().Format("2006-01-02T15:04:05Z")
	view := `{"name":"alice","state":"waiting","state_detail":"allow Bash?","account":"theta","cache_until":"` + until + `"}`
	for _, q := range []string{
		"INSERT INTO agent_state(agent, state, updated) VALUES('alice', 'running', 1)",
		"INSERT INTO agent_view(agent, view, updated) VALUES('alice', '" + view + "', 1)",
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	used := int64(250)
	e.Context = func(agent, account string) (string, *int64, int) {
		if agent != "alice" || account != "theta" {
			t.Fatalf("asked for %s on %s", agent, account)
		}
		return "claude-opus-5-5", &used, 1000
	}
	got, err := e.agentStrip(nil, map[string]any{"agent": "alice"})
	if err != nil {
		t.Fatal(err)
	}
	m := got.(map[string]any)
	left := m["cache_left_s"].(int64)
	if m["screen_word"] != "DIALOG" || m["screen_detail"] != "allow Bash?" || m["account"] != "theta" || m["model"] != "claude-opus-5-5" ||
		m["ctx_used"] != int64(250) || m["ctx_pct"] != 25 || m["ctx_limit"] != 1000 || left < 85 || left > 90 {
		t.Fatalf("%v", m)
	}
}

func TestAgentStripOfAnAgentWithNoStateOrAccountIsNulls(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	e.Context = func(string, string) (string, *int64, int) { t.Fatal("no account, nothing to read"); return "", nil, 0 }
	got, err := e.agentStrip(nil, map[string]any{"agent": "bob"})
	if err != nil {
		t.Fatal(err)
	}
	m := got.(map[string]any)
	for _, k := range []string{"model", "account", "ctx_pct", "ctx_used", "ctx_limit", "cache_left_s", "screen_word"} {
		if m[k] != nil {
			t.Fatalf("%s = %v", k, m[k])
		}
	}
	if m["screen_detail"] != "" {
		t.Fatalf("%v", m)
	}
	for _, params := range []map[string]any{{"agent": "nobody"}, {}} {
		if _, err := e.agentStrip(nil, params); err == nil {
			t.Fatalf("%v is refused", params)
		}
	}
}

func TestScreenWordIsTheWireStateReadBack(t *testing.T) {
	for _, c := range []struct{ state, detail, word, shown string }{
		{"running", "", "RUNNING", ""}, {"waiting", "x", "DIALOG", "x"}, {"stalled", "x", "FATAL", "x"},
		{"parked", "moving", "MOVING", ""}, {"parked", "13:05", "PENDING", "13:05"}, {"queued", "", "PENDING", ""},
		{"idle", "", "IDLE", ""}, {"no_terminal", "", "IDLE", ""},
	} {
		if w, d := screenWord(c.state, c.detail); w != c.word || d != c.shown {
			t.Errorf("%s/%q: %s %q", c.state, c.detail, w, d)
		}
	}
}
