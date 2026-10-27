package reads

import (
	"encoding/json"
	"github.com/handline/handline/go/internal/db"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/contracttest"
	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/store"
)

func testEnv(t *testing.T) *Env {
	t.Helper()
	// Run as the founder, whatever session runs the suite, with the protected store (the seals) under a stand-in user home.
	t.Setenv("HANDLINE_AGENT", "")
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	home := t.TempDir()
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	// The login check never asks the machine's keychain: an account is logged in when its directory has a .credentials.json.
	login := func(acct string) (bool, bool) {
		_, err := os.Stat(filepath.Join(home, "accounts", acct, ".credentials.json"))
		return err == nil, true
	}
	return &Env{DB: db, Home: home, Started: time.Now(), LoginState: login}
}

// writeAgentYAML is the founder's edit of an agent's file: written and sealed, so the roster trusts it.
func writeAgentYAML(t *testing.T, e *Env, name, text string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(e.Home, "agents", name, "agent.yaml"), []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := sealed.New(e.Home).Seal(sealed.AgentKey(name), text); err != nil {
		t.Fatal(err)
	}
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
	if s, _ := out.Agents[1]["status"].(map[string]any); s["state"] != "off" || s["reason"] != "none" {
		t.Fatalf("status %v", out.Agents[1]["status"])
	}
}

func TestTheWordForNoStoredViewIsOneOfTheSpecs(t *testing.T) {
	raw := contracttest.Spec(t)
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
	b := &board{begun: map[int64]bool{3: true}, sent: map[int64]bool{2: true, 4: true, 5: true}, blocked: map[int64]bool{4: true},
		current: map[string]int64{"w": 2, "x": 4}}
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
		{mk(2, "w", nil), "doing"},                       // a request is out and its owner holds it
		{mk(3, "human", nil), "doing"},                   // begun on a founder's message
		{mk(4, "x", nil), "queued"},                      // held, but an open blocker
		{mk(5, "w", nil), "queued"},                      // a request is out, but its owner holds another
		{mk(5, "y", nil), "queued"},                      // a request is out, but its owner holds none
		{mk(2, "w", row{"done_evidence": "ok"}), "done"}, // written states win
		{mk(2, "w", row{"discarded_by": "m", "done_evidence": "ok"}), "discarded"},
	} {
		if got := b.state(c.t); got != c.want {
			t.Errorf("%v: %s, want %s", c.t, got, c.want)
		}
	}
}

func TestABoardReadsATaskStartedAsItsOwnersOwnAsBegun(t *testing.T) {
	e := testEnv(t)
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'self', 'w', 'w', 1), (2, 'founder', 'w', 'w', 1), (3, 'plain', 'w', 'w', 1), (4, 'released', 'w', 'w', 1)",
		`INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'w', 'task_self_start', '{"task_id": 1}'), (1, 'w', 'task_begin', '{"task_id": 2}'), ` +
			`(1, 'w', 'task_begin', '{"task_id": 4}'), (2, 'x', 'release_task', '{"task_id": 4, "retired": []}')`,
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('w', 'IDLE', 1, 1)",
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	b, err := e.loadBoard()
	if err != nil {
		t.Fatal(err)
	}
	if !b.begun[1] || !b.begun[2] || b.begun[3] || b.begun[4] {
		t.Fatalf("begun is a task_self_start or a task_begin not released since, nothing else: %v", b.begun)
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

func TestAgentStripReadsTheStoredViewAndTheContextHook(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "alice", "")
	until := time.Now().Add(90 * time.Second).UTC().Format("2006-01-02T15:04:05Z")
	view := `{"name":"alice","state":"waiting","state_detail":"allow Bash?","account":"theta","cache_until":"` + until + `"}`
	for _, q := range []string{
		"INSERT INTO agent_state(agent, state, updated, account) VALUES('alice', 'running', 1, 'theta')",
		"INSERT INTO agent_view(agent, view, updated) VALUES('alice', '" + view + "', 1)",
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	used, pct := int64(250), 31
	e.Context = func(agent, account string) (string, *int, *int64, int) {
		if agent != "alice" || account != "theta" {
			t.Fatalf("asked for %s on %s", agent, account)
		}
		return "claude-opus-5-5", &pct, &used, 1000
	}
	got, err := e.agentStrip(nil, map[string]any{"agent": "alice"})
	if err != nil {
		t.Fatal(err)
	}
	m := got.(map[string]any)
	left := m["cache_left_s"].(int64)
	if m["screen_word"] != "DIALOG" || m["screen_detail"] != "allow Bash?" || m["account"] != "theta" || m["model"] != "claude-opus-5-5" ||
		m["ctx_used"] != int64(250) || m["ctx_pct"] != 31 || m["ctx_limit"] != 1000 || left < 85 || left > 90 {
		t.Fatalf("%v", m)
	}
}

func TestAgentStripOfAnAgentWithNoStateOrAccountIsNulls(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	e.Context = func(string, string) (string, *int, *int64, int) {
		t.Fatal("no account, nothing to read")
		return "", nil, nil, 0
	}
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

func TestAgentStripCtxPctIsTheStatusFilesAndNullWhenStale(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "alice", "")
	if _, err := e.DB.Exec("INSERT INTO agent_state(agent, state, updated, account) VALUES('alice', 'idle', 1, 'theta')"); err != nil {
		t.Fatal(err)
	}
	used := int64(900)
	var pct *int
	e.Context = func(string, string) (string, *int, *int64, int) { return "m", pct, &used, 1000 }
	ask := func() map[string]any {
		got, err := e.agentStrip(nil, map[string]any{"agent": "alice"})
		if err != nil {
			t.Fatal(err)
		}
		return got.(map[string]any)
	}
	if m := ask(); m["ctx_pct"] != nil || m["ctx_used"] != int64(900) || m["ctx_limit"] != 1000 {
		t.Fatalf("a stale file has no percent, not used over limit: %v", m)
	}
	p := 42
	pct = &p
	if m := ask(); m["ctx_pct"] != 42 {
		t.Fatalf("the file's own percent, though used/limit says 90: %v", m)
	}
}

func TestAgentStripAccountIsTheRowsNotAStoredViewsThatLags(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "alice", "")
	for _, q := range []string{
		"INSERT INTO agent_state(agent, state, updated, account) VALUES('alice', 'idle', 1, 'sigma')",
		`INSERT INTO agent_view(agent, view, updated) VALUES('alice', '{"state":"idle","account":"theta"}', 1)`,
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	got, err := e.agentStrip(nil, map[string]any{"agent": "alice"})
	if err != nil || got.(map[string]any)["account"] != "sigma" {
		t.Fatalf("%v %v", got, err)
	}
}

func TestAnInputHoldIsLostAfterTheConfiguredIdleTime(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "held", "")
	typed := time.Now().Unix() - 700
	if _, err := e.DB.Exec(`INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES('held', 'person', 'someone', ?, ?)`, typed, typed); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		config string
		held   bool
	}{
		{"", false},                          // unset: 600 s, and it was 700 ago
		{"input_hold_idle_s: 1000\n", true},  // longer than the default
		{"input_hold_idle_s: 60\n", false},   // shorter
		{"input_hold_idle_s: soon\n", false}, // not a number: the default
		{"input_hold_idle_s: 0\n", false},    // not positive: the default
		{"input_hold_idle_s: -5\n", false},
	} {
		if err := os.WriteFile(filepath.Join(e.Home, "config.yaml"), []byte(c.config), 0o644); err != nil {
			t.Fatal(err)
		}
		got, err := e.inputHolders("held")
		if err != nil {
			t.Fatal(err)
		}
		if holder := got[0].(map[string]any)["kind"] == "person"; holder != c.held {
			t.Errorf("config %q: holder kept %v, want %v (%v)", c.config, holder, c.held, got)
		}
	}
}

func TestTheBoardAgreesWithTheOneRule(t *testing.T) {
	e := testEnv(t)
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at) VALUES(1, 'held', 'w', 'm', 1, 'm', 2), (2, 'dep', 'x', 'm', 1, 'm', NULL), " +
			"(3, 'also sent', 'w', 'm', 1, 'm', 2), (4, 'nobody holds', 'y', 'm', 1, 'm', 2), (5, 'parent', 'y', 'm', 1, 'm', NULL), (6, 'child', 'z', 'y', 1, 'y', 2)",
		"UPDATE tasks SET parent_task = 5 WHERE id = 6",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(1, 2, 1), (5, 6, 1)",
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('w', 'IDLE', 1, 1), ('z', 'IDLE', 1, 6)",
	} {
		run(t, e, q)
	}
	b, err := e.loadBoard()
	if err != nil {
		t.Fatal(err)
	}
	rows, err := query(e.DB, "SELECT * FROM tasks ORDER BY id")
	if err != nil {
		t.Fatal(err)
	}
	doing := 0
	for _, r := range rows {
		task, _ := db.GetTask(e.DB, r.i("id"))
		want, _ := db.TaskState(e.DB, task)
		if got := b.state(r); got != want {
			t.Errorf("T%d: board %s, db %s", r.i("id"), got, want)
		}
		if want == "doing" {
			doing++
		}
	}
	if doing != 1 {
		t.Fatalf("%d doing, want only T6", doing)
	}
}
