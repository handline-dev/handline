package compose

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/claudecli"
	"github.com/handline/handline/go/internal/db"
	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/testkill"
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
	testkill.ExitWithParent()
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		v := os.Getenv("FAKE_CLAUDE_VERSION")
		if v == "" {
			v = claudecli.PinnedCLI
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
				five, seven := map[string]any{"utilization": 40.0}, map[string]any{"utilization": 10.0}
				var r5, r7 int64
				if n, _ := fmt.Sscanf(os.Getenv("FAKE_USAGE_RESETS"), "%d,%d", &r5, &r7); n == 2 { // when the windows end, unix seconds
					five["resets_at"], seven["resets_at"] = r5, r7
				}
				answer = map[string]any{"rate_limits_available": true, "rate_limits": map[string]any{"five_hour": five, "seven_day": seven}}
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

// rigConfigs is the Config each rig built its parts from, for the calls that take it again (Tick, Lifecycle, agentView).
var rigConfigs sync.Map

func cfgOf(p *Parts) Config { c, _ := rigConfigs.Load(p); return c.(Config) }

// rigWith is rig with the config edited before the parts are built.
func rigWith(t *testing.T, edit func(*Config)) (*Parts, string) {
	t.Helper()
	t.Setenv("FAKE_CLAUDE", "1")
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir()) // the daemon records each session's proof in the protected store, which an agent's session cannot write
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} {
		t.Setenv(k, "")
	}
	home := t.TempDir()
	os.MkdirAll(filepath.Join(home, "agents", "alice"), 0o755)
	os.WriteFile(filepath.Join(home, "agents", "alice", "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	os.MkdirAll(filepath.Join(home, "shared", ".claude"), 0o755)
	os.WriteFile(filepath.Join(home, "shared", ".claude", "settings.json"), []byte(sharedSettings), 0o644)
	os.MkdirAll(filepath.Join(home, "agents", "bob"), 0o755)
	os.WriteFile(filepath.Join(home, "agents", "bob", "agent.yaml"), []byte("vendor: claude\nrepo: "+t.TempDir()+"\n"), 0o644) // a folder that is no repository: a spawn here makes nothing
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	exe, _ := os.Executable()
	cfg := Config{DB: d, Home: home, CLI: exe, Env: []string{"FAKE_CLAUDE=1"}, Log: func(string, ...any) {}}
	edit(&cfg)
	if cfg.Term == nil {
		cfg.Term = newFakeTerminals().term() // no test reaches a real tmux
	}
	p := New(cfg)
	rigConfigs.Store(p, cfg)
	return p, home
}

func TestTheDaemonsLauncherNamesTheSettingsALaunchReads(t *testing.T) {
	p, home := rig(t)
	env := &supervisor.Env{DB: p.db, Home: home, Log: func(string, ...any) {}}
	lc := p.Lifecycle(cfgOf(p), LifecycleConfig{Env: env})
	parts, err := lc.Launch.FingerprintParts("bob") // panicked while the daemon's launcher had no team rules
	if err != nil || parts["team"] == "" {
		t.Fatalf("parts %v, err %v", parts, err)
	}
}

// withLifecycle builds the lifecycle over a bound rig: a session is not opened without it, since it says what the session is kept out of.
func withLifecycle(p *Parts, home string) {
	env := &supervisor.Env{DB: p.db, Home: home, Log: func(string, ...any) {}}
	p.Lifecycle(cfgOf(p), LifecycleConfig{Env: env})
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
	return wiredWith(t, newFakeTerminals())
}

// wiredWith is wired over the given terminals: no test reaches a real tmux.
func wiredWith(t *testing.T, ft *fakeTerminals) (*Parts, *Lifecycle, *supervisor.Env, string) {
	t.Helper()
	p, home := rig(t)
	dir, err := team.AccountConfigDir(home, "a1")
	if err != nil {
		t.Fatal(err)
	}
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, ".credentials.json"), []byte("{}"), 0o600) // only its presence is read
	wt := t.TempDir()
	d := p.db
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
	cfg := Config{DB: d, Home: home, CLI: cfgOf(p).CLI, Env: []string{"FAKE_CLAUDE=1"}, Log: func(string, ...any) {}, Term: ft.term()}
	rigConfigs.Store(p, cfg)
	lc := p.Lifecycle(cfg, LifecycleConfig{Env: env})
	env.Ports = p.SupervisorPorts(lc)
	p.Bind(d, env)
	return p, lc, env, wt
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

func TestADecisionAndAColdStartMeasurementAreReallyMade(t *testing.T) {
	p, lc, env, _ := wired(t)
	d := p.db
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

// A daemon that starts with a peer message to bob written and unconfirmed tells its sender, and telling a sender can start that agent, so the
// settle runs once the Env has its ports: run before them it took the lifecycle port as nil and the daemon panicked on every start.
func TestSettlingPeerSendsAtStartTellsTheSenderThroughTheWiredPorts(t *testing.T) {
	p, _, env, _ := wired(t)
	d := p.db
	rid, err := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "alice", Target: "bob", Body: "hello", Response: "async", Kind: "say"})
	if err != nil {
		t.Fatal(err)
	}
	key := fmt.Sprintf("send:bob:request:%d", rid)
	if err := dbpkg.SetCursor(d, key, 1); err != nil { // written to bob's session, not yet seen
		t.Fatal(err)
	}
	if !env.Wired() {
		t.Fatal("every supervisor port has its owner")
	}
	p.SettlePeerSends()
	if v, _ := dbpkg.Cursor(d, key); v != 3 {
		t.Fatalf("the unconfirmed send is unknown now, got %d", v)
	}
}
