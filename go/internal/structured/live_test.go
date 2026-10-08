package structured

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
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
	l := Launch{Path: path, Cwd: t.TempDir(), ConfigDir: cfg, SessionID: uuidFor(time.Now().String())}
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
	s.Send("live-2", "Run the shell command `ls` with the Bash tool and tell me the first entry.", Now)
	select {
	case r := <-asked:
		t.Logf("permission asked: %+v (state %q)", r, s.State())
	case <-c.Done():
		t.Error("no can_use_tool arrived")
	}
	waitIdle()
	t.Logf("state at the end: %q; frames %d", s.State(), len(frames))
}
