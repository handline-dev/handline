package structured

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"
)

// TestMain makes this test binary the fake Claude CLI when FAKE_CLAUDE is set: it speaks the stream-json protocol the real one
// does, as far as the session uses it.
func TestMain(m *testing.M) {
	if os.Getenv("FAKE_CLAUDE") == "1" {
		fakeClaude()
		return
	}
	os.Exit(m.Run())
}

func fakeClaude() {
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		fmt.Println(PinnedCLI + " (Claude Code)")
		return
	}
	out := json.NewEncoder(os.Stdout)
	var mu sync.Mutex
	emit := func(v any) { mu.Lock(); out.Encode(v); mu.Unlock() }
	state := func(s string) {
		emit(map[string]any{"type": "system", "subtype": "session_state_changed", "state": s, "uuid": "s", "session_id": "x"})
	}
	model, mode := "fake-model", "default"
	if os.Getenv("FAKE_DEAF") == "1" { // a CLI that ignores the end of stdin and leaves a child behind
		exec.Command("sleep", "60").Start()
	}
	perm := make(chan map[string]any, 1)
	hold := make(chan struct{}, 1)
	turn := func(uid, text string) {
		if os.Getenv("FAKE_SILENT") == "1" { // reads the message and says nothing
			return
		}
		if os.Getenv("FAKE_LIFECYCLE_ONLY") == "1" { // what the real CLI did when the model call failed: no echo, only lifecycle
			emit(map[string]any{"type": "command_lifecycle", "command_uuid": uid, "state": "queued"})
		} else {
			emit(map[string]any{"type": "user", "uuid": uid, "isReplay": true, "message": map[string]any{"role": "user", "content": text}})
		}
		state("running")
		switch {
		case text == "HOLD":
			<-hold // the turn runs until it is interrupted
		case text == "NEEDPERM":
			emit(map[string]any{"type": "control_request", "request_id": "perm1", "request": map[string]any{
				"subtype": "can_use_tool", "tool_name": "Bash", "tool_use_id": "tu1", "input": map[string]any{"command": "ls"}}})
			resp := <-perm
			emit(map[string]any{"type": "assistant", "message": map[string]any{"content": []any{map[string]any{"type": "text", "text": "tool " + fmt.Sprint(resp["behavior"])}}}})
		}
		emit(map[string]any{"type": "result", "subtype": "success"})
		state("idle")
	}
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		var m map[string]any
		json.Unmarshal(sc.Bytes(), &m)
		switch m["type"] {
		case "control_request":
			req := m["request"].(map[string]any)
			body := map[string]any{}
			switch req["subtype"] {
			case "set_model":
				if req["model"] != "ignored" { // the CLI says success and keeps the old one
					model = req["model"].(string)
				}
			case "set_permission_mode":
				mode = req["mode"].(string)
			case "get_settings":
				body = map[string]any{"effective": map[string]any{"model": model, "permissions": map[string]any{"defaultMode": mode}}}
			}
			emit(map[string]any{"type": "control_response", "response": map[string]any{"subtype": "success", "request_id": m["request_id"], "response": body}})
			if req["subtype"] == "interrupt" {
				select {
				case hold <- struct{}{}:
				default:
				}
			}
		case "control_response":
			perm <- m["response"].(map[string]any)["response"].(map[string]any)
		case "user":
			msg := m["message"].(map[string]any)
			go turn(m["uuid"].(string), msg["content"].(string))
		}
	}
	if os.Getenv("FAKE_DEAF") == "1" {
		select {}
	}
}

func launch(t *testing.T) Launch {
	t.Helper()
	exe, _ := os.Executable()
	t.Setenv("FAKE_CLAUDE", "1")
	return Launch{Path: exe, Cwd: t.TempDir(), ConfigDir: t.TempDir(), Env: []string{"FAKE_CLAUDE=1"}}
}

type events struct {
	mu  sync.Mutex
	all []map[string]any
}

func (e *events) add(m map[string]any) { e.mu.Lock(); e.all = append(e.all, m); e.mu.Unlock() }
func (e *events) count(pred func(map[string]any) bool) int {
	e.mu.Lock()
	defer e.mu.Unlock()
	n := 0
	for _, m := range e.all {
		if pred(m) {
			n++
		}
	}
	return n
}

func open(t *testing.T, h Handlers) (*Session, *events) {
	t.Helper()
	ev := &events{}
	h.OnEvent = ev.add
	s, err := Open(context.Background(), launch(t), h, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s, ev
}

func ctx(t *testing.T) context.Context {
	c, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)
	return c
}

func isEcho(uid string) func(map[string]any) bool {
	return func(m map[string]any) bool { return m["type"] == "user" && m["uuid"] == uid }
}

func TestAMessageIsDeliveredNowAndConfirmedByItsEcho(t *testing.T) {
	s, ev := open(t, Handlers{})
	r, err := s.Send("op-1", "hello", Now)
	if err != nil || r.State != Sent {
		t.Fatalf("%v %v", r, err)
	}
	if err := s.WaitConfirmed(ctx(t), "op-1"); err != nil {
		t.Fatal(err)
	}
	if ev.count(isEcho(r.UUID)) != 1 {
		t.Fatal("the CLI echoed the message once")
	}
}

func TestARetryWithTheSameOperationIdIsNeverSentTwice(t *testing.T) {
	s, ev := open(t, Handlers{})
	a, _ := s.Send("op-1", "hello", Now)
	b, err := s.Send("op-1", "hello", Now)
	if err != nil || a.UUID != b.UUID {
		t.Fatalf("%v %v", b, err)
	}
	s.WaitConfirmed(ctx(t), "op-1")
	time.Sleep(100 * time.Millisecond)
	if n := ev.count(func(m map[string]any) bool { return m["type"] == "user" }); n != 1 {
		t.Fatalf("%d user messages reached the CLI", n)
	}
}

func TestAQueuedMessageWaitsForTheTurnToEndAndThenGoes(t *testing.T) {
	s, ev := open(t, Handlers{})
	s.Send("op-1", "HOLD", Now)
	s.WaitConfirmed(ctx(t), "op-1")
	q, err := s.Send("op-2", "later", Queue)
	if err != nil || q.State != Queued {
		t.Fatalf("a queue send behind a running turn is held: %v %v", q, err)
	}
	time.Sleep(150 * time.Millisecond)
	if ev.count(isEcho(q.UUID)) != 0 {
		t.Fatal("the held message reached the CLI during the turn")
	}
	if err := s.Interrupt(ctx(t)); err != nil {
		t.Fatal(err)
	}
	if err := s.WaitConfirmed(ctx(t), "op-2"); err != nil {
		t.Fatalf("held message was not sent after the turn: %v", err)
	}
}

func TestANowMessageDuringATurnIsWrittenAtOnce(t *testing.T) {
	s, _ := open(t, Handlers{})
	s.Send("op-1", "HOLD", Now)
	s.WaitConfirmed(ctx(t), "op-1")
	r, _ := s.Send("op-2", "steer", Now)
	if r.State != Sent {
		t.Fatalf("%v", r)
	}
	if err := s.WaitConfirmed(ctx(t), "op-2"); err != nil {
		t.Fatal(err)
	}
	s.Interrupt(ctx(t))
}

func assistantText(ev *events) func() string {
	return func() string {
		ev.mu.Lock()
		defer ev.mu.Unlock()
		for _, m := range ev.all {
			if m["type"] == "assistant" {
				if t := Translate(m); len(t) > 0 {
					return t[0].Data["text"].(string)
				}
			}
		}
		return ""
	}
}

func waitFor(t *testing.T, f func() string, want string) {
	t.Helper()
	for i := 0; i < 100; i++ {
		if f() == want {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("got %q, want %q", f(), want)
}

func TestAPermissionPromptIsAnsweredByTheCallback(t *testing.T) {
	var got ToolRequest
	s, ev := open(t, Handlers{CanUseTool: func(_ context.Context, r ToolRequest) (Decision, error) {
		got = r
		return Decision{Allow: true}, nil
	}})
	s.Send("op-1", "NEEDPERM", Now)
	waitFor(t, assistantText(ev), "tool allow")
	if got.ToolName != "Bash" || got.Input["command"] != "ls" || got.ToolUse != "tu1" {
		t.Fatalf("%+v", got)
	}
}

func TestAPermissionPromptIsDeniedWhenNoOneAnswersOrTheCallbackFails(t *testing.T) {
	s, ev := open(t, Handlers{})
	s.Send("op-1", "NEEDPERM", Now)
	waitFor(t, assistantText(ev), "tool deny")

	s2, ev2 := open(t, Handlers{CanUseTool: func(context.Context, ToolRequest) (Decision, error) {
		return Decision{Allow: true}, fmt.Errorf("boom")
	}})
	s2.Send("op-1", "NEEDPERM", Now)
	waitFor(t, assistantText(ev2), "tool deny")
}

func TestTheChildSeesOnlyTheConfigDirAndAFewPlainVariables(t *testing.T) {
	t.Setenv("ANTHROPIC_API_KEY", "secret")
	l := Launch{ConfigDir: "/cfg"}
	env := strings.Join(l.env(), "\n")
	if strings.Contains(env, "ANTHROPIC") || !strings.Contains(env, "CLAUDE_CONFIG_DIR=/cfg") {
		t.Fatal(env)
	}
}

func TestAnUnpinnedCLIIsRefused(t *testing.T) {
	l := launch(t)
	if err := CheckVersion(ctx(t), l.Path, PinnedCLI); err != nil {
		t.Fatal(err)
	}
	if err := CheckVersion(ctx(t), l.Path, "0.0.1"); err == nil {
		t.Fatal("a different version is refused")
	}
}

func TestAnOperationIdIsAValidUUIDAndTheSameEachTime(t *testing.T) {
	a := uuidFor("x")
	if a != uuidFor("x") || a == uuidFor("y") || len(a) != 36 || a[14] != '5' {
		t.Fatal(a)
	}
}
