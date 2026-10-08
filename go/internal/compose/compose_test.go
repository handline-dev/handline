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

	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/transcript"
)

// TestMain makes this test binary the fake Claude CLI when FAKE_CLAUDE is set: it answers --version and every control request, echoes each
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
		fmt.Println(structured.PinnedCLI + " (Claude Code)")
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
			emit(map[string]any{"type": "control_response", "response": map[string]any{"subtype": "success", "request_id": m["request_id"], "response": map[string]any{}}})
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

func rig(t *testing.T) (*Parts, string) {
	t.Helper()
	t.Setenv("FAKE_CLAUDE", "1")
	home := t.TempDir()
	os.MkdirAll(filepath.Join(home, "agents", "alice"), 0o755)
	os.WriteFile(filepath.Join(home, "agents", "alice", "agent.yaml"), []byte("vendor: claude\nmode: structured # the real CLI\n"), 0o644)
	os.MkdirAll(filepath.Join(home, "agents", "bob"), 0o755)
	os.WriteFile(filepath.Join(home, "agents", "bob", "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	exe, _ := os.Executable()
	p := New(Config{DB: d, Home: home, CLI: exe, Env: []string{"FAKE_CLAUDE=1"}, ConfigDir: func(string) string { return t.TempDir() }, Log: func(string, ...any) {}})
	p.Sink.DB = d
	p.Sink.Err = func(agent string, err error) { t.Error(agent, err) }
	return p, home
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

func TestAStructuredSessionStartsTakesAMessageAndStopsThroughTheRealWiring(t *testing.T) {
	p, _ := rig(t)
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
