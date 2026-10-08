package structured

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// TestLiveCLI runs one short turn and one permission prompt against the real Claude CLI. It uses the account's usage, so it only
// runs when LIVE_CLAUDE_CONFIG names the account's config dir: LIVE_CLAUDE_CONFIG=<dir> go test -run TestLiveCLI -v ./internal/structured.
// It logs every frame, which is the evidence for the design note's unverified list.
func TestLiveCLI(t *testing.T) {
	cfg := os.Getenv("LIVE_CLAUDE_CONFIG")
	if cfg == "" {
		t.Skip("LIVE_CLAUDE_CONFIG is not set")
	}
	path, err := exec.LookPath("claude")
	if err != nil {
		t.Fatal(err)
	}
	if err := CheckVersion(context.Background(), path, PinnedCLI); err != nil {
		t.Log("version:", err)
	}
	var mu sync.Mutex
	var frames []map[string]any
	asked := make(chan ToolRequest, 4)
	h := Handlers{
		OnEvent: func(m map[string]any) {
			mu.Lock()
			frames = append(frames, m)
			mu.Unlock()
			b, _ := json.Marshal(m)
			if len(b) > 400 {
				b = b[:400]
			}
			t.Logf("frame %s", b)
		},
		CanUseTool: func(_ context.Context, r ToolRequest) (Decision, error) {
			asked <- r
			return Decision{Allow: true}, nil
		},
		OnExit: func(err error) { t.Logf("exit: %v", err) },
	}
	// The account's allow list (Bash(ls:*) on one account) would answer a prompt before can_use_tool fires, so the check does not
	// depend on it twice over: only the project settings are read (an empty temp dir), and the command has a name nothing allows.
	cwd := t.TempDir()
	l := Launch{Path: path, Cwd: cwd, ConfigDir: cfg, SessionID: UUIDFor(time.Now().String()), Args: []string{"--setting-sources", "project"}}
	s, err := Open(context.Background(), l, h, 60*time.Second)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer s.Close()
	c, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	r, err := s.Send("live-1", "Reply with the single word ok.", Now)
	t.Logf("send: %+v %v", r, err)
	if err := s.WaitConfirmed(c, "live-1"); err != nil {
		t.Errorf("no echo of our uuid (isReplay): %v", err)
	}
	waitIdle := func() {
		for s.Busy() && c.Err() == nil {
			time.Sleep(200 * time.Millisecond)
		}
	}
	waitIdle()
	t.Logf("state after turn 1: %q", s.State())
	marker := filepath.Join(cwd, "live-check-"+UUIDFor(time.Now().String())[:8])
	s.Send("live-2", "Run the shell command `touch "+marker+"` with the Bash tool and tell me when it is done.", Now)
	select {
	case r := <-asked:
		t.Logf("permission asked: %+v (state %q)", r, s.State())
	case <-c.Done():
		t.Error("no can_use_tool arrived")
	}
	waitIdle()
	t.Logf("state at the end: %q; frames %d", s.State(), len(frames))
}

// TestLiveGetUsage asks a real CLI for an account's usage with the get_usage control request: no model turn, no session spoken in. It
// is the live check for the usage port. LIVE_CLAUDE_CONFIG=<dir> go test -run TestLiveGetUsage -v ./internal/structured
func TestLiveGetUsage(t *testing.T) {
	cfg := os.Getenv("LIVE_CLAUDE_CONFIG")
	if cfg == "" {
		t.Skip("LIVE_CLAUDE_CONFIG is not set")
	}
	path, err := exec.LookPath("claude")
	if err != nil {
		t.Fatal(err)
	}
	s, err := Open(context.Background(), Launch{Path: path, Cwd: t.TempDir(), ConfigDir: cfg}, Handlers{
		OnEvent: func(m map[string]any) {
			if m["type"] == "rate_limit_event" {
				b, _ := json.Marshal(m)
				t.Logf("rate_limit_event %s", b)
			}
		}}, 60*time.Second)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer s.Close()
	c, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	r, err := s.GetUsage(c)
	if err != nil {
		t.Fatalf("get_usage: %v", err)
	}
	b, _ := json.MarshalIndent(r, "", " ")
	t.Logf("get_usage answered:\n%s", b)
	if avail, _ := r["rate_limits_available"].(bool); !avail {
		t.Error("rate_limits_available is false: the plan limits did not come back")
	}
	limits, _ := r["rate_limits"].(map[string]any)
	for _, w := range []string{"five_hour", "seven_day"} {
		if m, _ := limits[w].(map[string]any); m == nil || m["utilization"] == nil || m["resets_at"] == nil {
			t.Errorf("%s has no utilization and reset: %v", w, limits[w])
		}
	}
	// A second ask must not take a turn or fail: the poll asks once per account per cycle.
	if _, err := s.GetUsage(c); err != nil {
		t.Errorf("second get_usage: %v", err)
	}
}

// TestLiveHooks checks two facts the composition root relies on: hooks in a --settings file load under --setting-sources project,
// and under stream-json the Stop hook fires at the end of a turn and a PreToolUse hook fires and can refuse. The hooks are tiny
// scripts that append a line to a temp file. LIVE_CLAUDE_CONFIG=<dir> go test -run TestLiveHooks -v ./internal/structured
func TestLiveHooks(t *testing.T) {
	cfg := os.Getenv("LIVE_CLAUDE_CONFIG")
	if cfg == "" {
		t.Skip("LIVE_CLAUDE_CONFIG is not set")
	}
	path, err := exec.LookPath("claude")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	log := filepath.Join(dir, "hooks.log")
	stop := filepath.Join(dir, "stop.sh")
	guard := filepath.Join(dir, "guard.sh")
	const deny = `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"live-hooks guard"}}`
	if err := os.WriteFile(stop, []byte("#!/bin/sh\ncat >/dev/null\necho stop >> '"+log+"'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	// The guard refuses any call whose input names the fixed file, and lets the rest through.
	if err := os.WriteFile(guard, []byte("#!/bin/sh\nin=$(cat)\ncase \"$in\" in\n*live-hooks-refused*) echo refused >> '"+log+"'\nprintf '%s' '"+deny+"';;\n*) echo pretool >> '"+log+"';;\nesac\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	settings := filepath.Join(dir, "hooks-settings.json")
	cfgJSON := map[string]any{"hooks": map[string]any{
		"Stop":       []any{map[string]any{"hooks": []any{map[string]any{"type": "command", "command": stop}}}},
		"PreToolUse": []any{map[string]any{"matcher": "Bash", "hooks": []any{map[string]any{"type": "command", "command": guard}}}},
	}}
	b, _ := json.Marshal(cfgJSON)
	if err := os.WriteFile(settings, b, 0o644); err != nil {
		t.Fatal(err)
	}
	h := Handlers{
		OnEvent: func(m map[string]any) {
			b, _ := json.Marshal(m)
			if len(b) > 300 {
				b = b[:300]
			}
			t.Logf("frame %s", b)
		},
		CanUseTool: func(_ context.Context, r ToolRequest) (Decision, error) { return Decision{Allow: true}, nil },
		OnExit:     func(err error) { t.Logf("exit: %v", err) },
	}
	cwd := t.TempDir()
	l := Launch{Path: path, Cwd: cwd, ConfigDir: cfg, SessionID: uuidFor(time.Now().String()),
		Args: []string{"--setting-sources", "project", "--settings", settings}}
	s, err := Open(context.Background(), l, h, 60*time.Second)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer s.Close()
	c, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	waitIdle := func() {
		for s.Busy() && c.Err() == nil {
			time.Sleep(200 * time.Millisecond)
		}
	}
	s.Send("hook-1", "Run the shell command `touch "+filepath.Join(cwd, "live-hooks-refused")+"` with the Bash tool and tell me what happened.", Now)
	waitIdle()
	time.Sleep(2 * time.Second) // a Stop hook may land just after the turn's result frame
	got, _ := os.ReadFile(log)
	t.Logf("hooks.log:\n%s", got)
	if _, err := os.Stat(filepath.Join(cwd, "live-hooks-refused")); err == nil {
		t.Error("the guarded file was created: PreToolUse did not refuse")
	}
	lines := string(got)
	if !strings.Contains(lines, "refused") {
		t.Error("no refusal line: the PreToolUse hook from --settings did not fire under --setting-sources project")
	}
	if !strings.Contains(lines, "stop") {
		t.Error("no stop line: the Stop hook did not fire at the end of the turn")
	}
}
