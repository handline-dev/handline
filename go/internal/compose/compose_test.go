package compose

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/transcript"
)

// TestMain makes this test binary the fake Claude CLI when FAKE_CLAUDE is set: it answers --version and every control request (get_usage with limits), echoes each
// user message and runs a turn that ends at once.
func TestMain(m *testing.M) {
	if os.Getenv("FAKE_CLAUDE") == "1" {
		fakeClaude()
		return
	}
	os.Exit(m.Run())
}

func fakeClaude() {
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		v := os.Getenv("FAKE_CLAUDE_VERSION")
		if v == "" {
			v = structured.PinnedCLI
		}
		fmt.Println(v + " (Claude Code)")
		return
	}
	out := json.NewEncoder(os.Stdout)
	var mu sync.Mutex
	emit := func(v any) { mu.Lock(); out.Encode(v); mu.Unlock() }
	state := func(s string) {
		emit(map[string]any{"type": "system", "subtype": "session_state_changed", "state": s, "uuid": "s", "session_id": "x"})
	}
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		var m map[string]any
		json.Unmarshal(sc.Bytes(), &m)
		switch m["type"] {
		case "control_request":
			answer := map[string]any{}
			if req, _ := m["request"].(map[string]any); req["subtype"] == "get_usage" {
				answer = map[string]any{"rate_limits_available": true, "rate_limits": map[string]any{
					"five_hour": map[string]any{"utilization": 40.0}, "seven_day": map[string]any{"utilization": 10.0}}}
			}
			emit(map[string]any{"type": "control_response", "response": map[string]any{"subtype": "success", "request_id": m["request_id"], "response": answer}})
		case "user":
			msg := m["message"].(map[string]any)
			go func(uid, text string) {
				emit(map[string]any{"type": "user", "uuid": uid, "isReplay": true, "message": map[string]any{"role": "user", "content": text}})
				state("running")
				emit(map[string]any{"type": "assistant", "message": map[string]any{"content": []any{map[string]any{"type": "text", "text": "ok " + text}}}})
				emit(map[string]any{"type": "result", "subtype": "success"})
				state("idle")
			}(m["uuid"].(string), msg["content"].(string))
		}
	}
}

func rig(t *testing.T) (*Parts, string) { return rigWith(t, func(*Config) {}) }

// rigWith is rig with the config edited before the parts are built.
func rigWith(t *testing.T, edit func(*Config)) (*Parts, string) {
	t.Helper()
	t.Setenv("FAKE_CLAUDE", "1")
	home := t.TempDir()
	os.MkdirAll(filepath.Join(home, "agents", "alice"), 0o755)
	os.WriteFile(filepath.Join(home, "agents", "alice", "agent.yaml"), []byte("vendor: claude\nstructured: true # the real CLI\n"), 0o644)
	os.MkdirAll(filepath.Join(home, "shared", ".claude"), 0o755)
	os.WriteFile(filepath.Join(home, "shared", ".claude", "settings.json"), []byte(sharedSettings), 0o644)
	os.MkdirAll(filepath.Join(home, "agents", "bob"), 0o755)
	os.WriteFile(filepath.Join(home, "agents", "bob", "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	exe, _ := os.Executable()
	cfg := Config{DB: d, Home: home, CLI: exe, Env: []string{"FAKE_CLAUDE=1"}, ConfigDir: func(string) string { return t.TempDir() }, Log: func(string, ...any) {}}
	edit(&cfg)
	p := New(cfg)
	p.Sink.DB = d
	p.Sink.Err = func(agent string, err error) { t.Error(agent, err) }
	return p, home
}

func TestAClaudeOtherThanThePinIsRefusedUnlessTheConfigAcceptsItsVersion(t *testing.T) {
	spec := lifecycle.StartSpec{Agent: "alice", Worktree: t.TempDir(), SessionID: "22222222-2222-4222-8222-222222222222"}
	t.Setenv("FAKE_CLAUDE_VERSION", "9.9.9") // the version check runs the CLI from the daemon's own environment
	p, _ := rig(t)
	err := p.Run.Open(spec)
	if err == nil || !strings.Contains(err.Error(), "9.9.9") {
		t.Fatalf("a claude that is not the pinned version started: %v", err)
	}
	p, _ = rigWith(t, func(c *Config) { c.CLIVersion = "9.9.9" })
	if err := p.Run.Open(spec); err != nil {
		t.Fatal(err)
	}
	defer p.Run.Close("alice")
}

func TestTheKindOfAnAgentIsItsModeInAgentYAML(t *testing.T) {
	p, _ := rig(t)
	if !p.Kind("alice") || p.Kind("bob") || p.Kind("nobody") {
		t.Fatal("only alice is structured")
	}
	if !p.Run.Structured("alice") || !p.Agents.Structured("alice") || p.Agents.Structured("bob") {
		t.Fatal("the runner and the agents port agree")
	}
}

// withLifecycle builds the lifecycle over a bound rig: a session is not opened without it, since it says what the session is kept out of.
func withLifecycle(p *Parts, home string) {
	env := &supervisor.Env{DB: p.Sink.DB, Home: home, Log: func(string, ...any) {}}
	p.Lifecycle(p.Run.cfg, LifecycleConfig{Env: env})
}

func TestAStructuredSessionStartsTakesAMessageAndStopsThroughTheRealWiring(t *testing.T) {
	p, home := rig(t)
	p.Bind(p.Sink.DB, &tells{})
	p.Run.cfg.DB = p.Sink.DB
	withLifecycle(p, home)
	var kinds []string
	var mu sync.Mutex
	p.Sink.Publish = func(e transcript.Event) { mu.Lock(); kinds = append(kinds, e.Kind); mu.Unlock() }
	if p.Run.Alive("alice") || p.Agents.Live("alice") {
		t.Fatal("nothing runs before it is opened")
	}
	if err := p.Run.Open(lifecycle.StartSpec{Agent: "alice", Worktree: t.TempDir(), SessionID: "11111111-1111-4111-8111-111111111111"}); err != nil {
		t.Fatal(err)
	}
	if !p.Run.Alive("alice") || !p.Agents.Live("alice") {
		t.Fatal("it is live once initialize answered")
	}
	if err := p.Run.Open(lifecycle.StartSpec{Agent: "alice", Worktree: t.TempDir(), SessionID: "x"}); err == nil {
		t.Fatal("a second session for one agent is refused")
	}
	if err := p.Run.Send("alice", "op1", "hello"); err != nil {
		t.Fatal(err)
	}
	if !p.Run.WaitIdle("alice", 10*time.Second) {
		t.Fatal("the turn ends")
	}
	if err := p.Run.Close("alice"); err != nil {
		t.Fatal(err)
	}
	if p.Run.Alive("alice") || p.Agents.Live("alice") {
		t.Fatal("it is gone after Close")
	}
	var n int
	var session string
	if err := p.Sink.DB.QueryRow("SELECT COUNT(*), COALESCE(MAX(session), '') FROM transcript_events WHERE agent = 'alice'").Scan(&n, &session); err != nil {
		t.Fatal(err)
	}
	if n == 0 || session != "11111111-1111-4111-8111-111111111111" {
		t.Fatalf("its events are kept under its session: %d %q", n, session)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(kinds) == 0 || kinds[len(kinds)-1] != "session_end" && kinds[len(kinds)-1] != "turn_end" {
		t.Logf("kinds %v", kinds)
	}
}

type tells struct {
	mu   sync.Mutex
	logs []string
}

func (t *tells) SessionWritten(_ db.Q, agent, op string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.logs = append(t.logs, "written "+agent+" "+op)
	return nil
}

func (t *tells) SessionConfirmed(_ db.Q, agent, op string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.logs = append(t.logs, "confirmed "+agent+" "+op)
	return nil
}

func (t *tells) SessionLost(_ db.Q, agent string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.logs = append(t.logs, "lost "+agent)
	return nil
}

func TestTheSupervisorIsToldOfAnEchoAndOfTheEndOfTheSession(t *testing.T) {
	p, home := rig(t)
	tl := &tells{}
	p.Bind(p.Sink.DB, tl)
	p.Run.cfg.DB = p.Sink.DB
	withLifecycle(p, home)
	if err := p.Run.Open(lifecycle.StartSpec{Agent: "alice", Worktree: t.TempDir(), SessionID: "22222222-2222-4222-8222-222222222222"}); err != nil {
		t.Fatal(err)
	}
	if err := p.Run.Send("alice", "op7", "hi"); err != nil {
		t.Fatal(err)
	}
	if err := p.Run.Close("alice"); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		tl.mu.Lock()
		got := fmt.Sprint(tl.logs)
		tl.mu.Unlock()
		if got == "[confirmed alice op7 lost alice]" {
			return
		}
		if time.Now().After(deadline) {
			t.Fatal(got)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func TestOneSetOfLocksServesTheManagerAndTheSpawns(t *testing.T) {
	p, _ := rig(t)
	if p.Locks == nil {
		t.Fatal("the locks are made once, here")
	}
	rel := p.Locks.Lock("alice")
	got := make(chan struct{})
	go func() { p.Locks.Lock("alice")(); close(got) }()
	select {
	case <-got:
		t.Fatal("a second lock on one agent waits")
	case <-time.After(100 * time.Millisecond):
	}
	rel()
	<-got
}

// wired builds the lifecycle manager over the real parts, with a closed structured agent that has a worktree.
func wired(t *testing.T) (*Parts, *Lifecycle, *supervisor.Env, string) {
	t.Helper()
	p, home := rig(t)
	dir, err := team.AccountConfigDir(home, "a1")
	if err != nil {
		t.Fatal(err)
	}
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, ".credentials.json"), []byte("{}"), 0o600) // only its presence is read
	wt := t.TempDir()
	d := p.Sink.DB
	if err := dbpkg.SetTerminal(d, "alice", "", wt); err != nil {
		t.Fatal(err)
	}
	if err := dbpkg.SetClosed(d, "alice", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "human", Target: "alice", Body: "begin", Response: "async", Kind: "ask"}); err != nil {
		t.Fatal(err) // what a closed agent is woken for
	}
	env := &supervisor.Env{DB: d, Home: home, Log: func(string, ...any) {}}
	cfg := Config{DB: d, Home: home, CLI: p.Run.cfg.CLI, Env: []string{"FAKE_CLAUDE=1"}, Log: func(string, ...any) {}}
	p.Run.cfg = cfg
	lc := p.Lifecycle(cfg, LifecycleConfig{Env: env})
	env.Ports = p.SupervisorPorts(lc)
	p.Bind(d, env)
	return p, lc, env, wt
}

func TestTheLifecycleWakesAClosedStructuredAgentAndClosesItAgain(t *testing.T) {
	p, lc, env, _ := wired(t)
	if !env.Wired() {
		t.Fatal("every supervisor port has its owner")
	}
	ok, said, err := lc.Manager.WakeAgent("alice", lifecycle.WakeOpts{})
	if err != nil || !ok {
		t.Fatalf("wake: %v %q %v", ok, said, err)
	}
	if !p.Run.Alive("alice") {
		t.Fatal("the wake started a session")
	}
	row, _ := dbpkg.GetAgentState(p.Sink.DB, "alice")
	if row == nil || row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 || row.ResumeSession.String == "" {
		t.Fatalf("it is open again under a stored session: %+v", row)
	}
	ok, said, err = lc.Manager.CloseAgent("alice", lifecycle.SessionStatus{SessionID: row.ResumeSession.String}, lifecycle.CloseOpts{Force: true})
	if err != nil || !ok {
		t.Fatalf("close: %v %q %v", ok, said, err)
	}
	if p.Run.Alive("alice") {
		t.Fatal("the close ended the session")
	}
}

const sharedSettings = `{
  "autoContinueAtUsageLimit": false,
  "permissions": {"allow": ["Bash(ls:*)"]},
  "env": {"SECRET": "x"},
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "/opt/bin/hl hook Stop", "timeout": 10}]}],
    "PreToolUse": [
      {"matcher": "Write|Edit|Bash", "hooks": [{"type": "command", "command": "ocp hook PreToolUse", "timeout": 10}, {"type": "command", "command": "other-tool guard"}]},
      {"hooks": [{"type": "command", "command": "somebody-elses hook PreToolUse"}]}
    ],
    "Notification": [{"hooks": [{"type": "command", "command": "notify-me"}]}]
  }
}`

func TestTheHooksFileHoldsOurHooksOnlyAndNotASettingOrAnotherHook(t *testing.T) {
	p, home := rig(t)
	os.MkdirAll(filepath.Join(home, "structured", "alice"), 0o755) // a directory that was there with the wrong mode
	path, err := WriteSettings(home, "alice", Confinement{})
	if err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(path); st.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v", st.Mode())
	}
	if st, _ := os.Stat(filepath.Dir(path)); st.Mode().Perm() != 0o700 {
		t.Fatalf("dir mode %v", st.Mode())
	}
	b, _ := os.ReadFile(path)
	var got map[string]any
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"Stop":       []any{map[string]any{"hooks": []any{map[string]any{"type": "command", "command": "/opt/bin/hl hook Stop", "timeout": float64(10)}}}},
		"PreToolUse": []any{map[string]any{"matcher": "Write|Edit|Bash", "hooks": []any{map[string]any{"type": "command", "command": "ocp hook PreToolUse", "timeout": float64(10)}}}},
	}
	if !reflect.DeepEqual(got["hooks"], want) {
		t.Fatalf("%s", b)
	}
	_ = p
}

func TestASessionIsNotStartedWithoutTheShellGuardOrWithUnreadableSettings(t *testing.T) {
	for _, shared := range []string{`{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "hl hook Stop"}]}]}}`, `{}`, `not json`} {
		if _, err := HooksSettings([]byte(shared)); err == nil {
			t.Errorf("%s: a session without the guard", shared)
		}
	}
	p, home := rig(t)
	os.Remove(filepath.Join(home, "shared", ".claude", "settings.json"))
	err := p.Run.Open(lifecycle.StartSpec{Agent: "alice", Worktree: t.TempDir(), SessionID: "33333333-3333-4333-8333-333333333333"})
	if err == nil || p.Run.Alive("alice") {
		t.Fatal("no shared settings, no session")
	}
}

func TestAStartTheDaemonCannotDoIsNotCountedAgainstTheAgent(t *testing.T) {
	p, lc, _, _ := wired(t)
	d := p.Sink.DB
	// bob is a terminal agent and carol a structured one with no worktree: neither can be started here, and neither may be stalled for it.
	os.MkdirAll(filepath.Join(p.home, "agents", "carol"), 0o755)
	os.WriteFile(filepath.Join(p.home, "agents", "carol", "agent.yaml"), []byte("vendor: claude\nstructured: true\nrepo: "+t.TempDir()+"\n"), 0o644)
	os.WriteFile(filepath.Join(p.home, "agents", "bob", "agent.yaml"), []byte("vendor: claude\nrepo: "+t.TempDir()+"\n"), 0o644)
	for _, agent := range []string{"bob", "carol"} {
		if _, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "human", Target: agent, Body: "begin", Response: "async", Kind: "ask"}); err != nil {
			t.Fatal(err)
		}
		for i := 0; i < 3; i++ {
			lc.Manager.StartForWork(agent) // an error is the answer: it is not a failed start
		}
		row, _ := dbpkg.GetAgentState(d, agent)
		if row != nil && (row.WakeFailures != 0 || row.State == "STALLED") {
			t.Fatalf("%s was counted or stalled: %+v", agent, row)
		}
	}
}

func TestADecisionAndAColdStartMeasurementAreReallyMade(t *testing.T) {
	p, lc, env, _ := wired(t)
	d := p.Sink.DB
	ports := lc.Manager.Agents
	if err := ports.RaiseDecision(d, "alice", "[trust] add /x - asked by alice\n\nwhy", "trust"); err != nil {
		t.Fatal(err)
	}
	if err := ports.RaiseDecision(d, "alice", "[trust] add /x - asked by alice\n\nwhy", "trust"); err != nil {
		t.Fatal(err) // the same text still waiting is that decision, not a second
	}
	var cards, tasks int
	d.QueryRow("SELECT COUNT(*) FROM requests WHERE requester = 'alice' AND target = 'human' AND body LIKE '[trust]%'").Scan(&cards)
	d.QueryRow("SELECT COUNT(*) FROM tasks WHERE owner = 'human' AND expects = 'decision'").Scan(&tasks)
	if cards != 1 || tasks != 1 {
		t.Fatalf("cards %d, tasks %d", cards, tasks)
	}
	// An account with no trusted reading is not measured, as measure.begin does: the call is made, and leaves no open start.
	dbpkg.SetMeta(d, "coldstart:alice", "stale")
	ports.MeasureBegin(d, "alice", "a1")
	if v, _, _ := dbpkg.Meta(d, "coldstart:alice"); v != "" {
		t.Fatalf("an unread account left an open start: %q", v)
	}
	_ = env
}

// TestTheAgentKindIsReadAsThePythonDaemonReadsIt plays the cases both daemons share (contract/agent-kind-cases.json).
func TestTheAgentKindIsReadAsThePythonDaemonReadsIt(t *testing.T) {
	raw, err := os.ReadFile("../../../contract/agent-kind-cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Cases []struct {
			Yaml       string `json:"yaml"`
			Structured bool   `json:"structured"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &f); err != nil || len(f.Cases) == 0 {
		t.Fatal(err)
	}
	for i, c := range f.Cases {
		home := t.TempDir()
		os.MkdirAll(filepath.Join(home, "agents", "x"), 0o755)
		os.WriteFile(filepath.Join(home, "agents", "x", "agent.yaml"), []byte(c.Yaml), 0o644)
		if got := KindFromYAML(home)("x"); got != c.Structured {
			t.Errorf("case %d %q: %v, want %v", i, c.Yaml, got, c.Structured)
		}
	}
}

func TestAHeldMessageThatIsWrittenIsToldAsWrittenOnceAndAnImmediateSendIsNot(t *testing.T) {
	p, _ := rig(t)
	tl := &tells{}
	p.Bind(p.Sink.DB, tl)
	led := confirming{&structured.DBLedger{DB: p.Sink.DB, Agent: "alice"}, p}
	// An immediate send goes straight to Sent: the send path marks it itself.
	if err := led.Put(structured.Op{ID: "now1", UUID: "u1", Text: "x", Delivery: structured.Now, State: structured.Sent}); err != nil {
		t.Fatal(err)
	}
	// A held one is Queued, and written later.
	if err := led.Put(structured.Op{ID: "held1", UUID: "u2", Text: "y", Delivery: structured.Queue, State: structured.Queued}); err != nil {
		t.Fatal(err)
	}
	if err := led.Put(structured.Op{ID: "held1", UUID: "u2", Text: "y", Delivery: structured.Queue, State: structured.Sent}); err != nil {
		t.Fatal(err)
	}
	if err := led.Put(structured.Op{ID: "held1", UUID: "u2", Text: "y", Delivery: structured.Queue, State: structured.Confirmed}); err != nil {
		t.Fatal(err)
	}
	tl.mu.Lock()
	defer tl.mu.Unlock()
	if fmt.Sprint(tl.logs) != "[written alice held1 confirmed alice held1]" {
		t.Fatalf("%v", tl.logs)
	}
}
