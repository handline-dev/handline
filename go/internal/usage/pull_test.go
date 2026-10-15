package usage

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/paths"
)

func writeTranscript(t *testing.T, home, acct, cwd, sid string, lines ...string) string {
	t.Helper()
	dir := filepath.Join(paths.AccountDir(home, acct), ".claude", "projects", notAlnum.ReplaceAllString(cwd, "-"))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(dir, sid+".jsonl")
	text := ""
	for _, l := range lines {
		text += l + "\n"
	}
	if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestAPulledSnapshotIsTheLastRealTurnOfTheSessionsOwnTranscript(t *testing.T) {
	home := t.TempDir()
	turn := func(model string, in, cacheRead int) string {
		b, _ := json.Marshal(map[string]any{"type": "assistant", "message": map[string]any{"model": model,
			"usage": map[string]any{"input_tokens": in, "cache_read_input_tokens": cacheRead, "service_tier": "standard"}}})
		return string(b)
	}
	writeTranscript(t, home, "alpha", "/w/a", "s1",
		turn("claude-opus-5-5", 100, 285_696),
		`{"type":"cost-state","totalCostUSD":1.5,"totalLinesAdded":7,"totalLinesRemoved":2}`,
		turn("<synthetic>", 0, 0),                 // an interrupt notice: not the session's context
		`{"type":"assistant","message":{"usage":`) // half-written by a turn under way
	snap := PullSnapshot(home, "a1", "/w/a", "", 50)
	if snap == nil || snap["account"] != "alpha" || snap["session_id"] != "s1" || snap["model_id"] != "claude-opus-5-5" ||
		snap["ctx_pct"] != 29 || snap["ctx_size"] != int64(1_000_000) || snap["cost_usd"] != 1.5 || snap["source"] != "pull" {
		t.Fatalf("%v", snap)
	}
	if used := snap["ctx_used"].(map[string]int64); len(used) != 2 || used["cache_read_input_tokens"] != 285_696 {
		t.Fatalf("only the four counts, those given: %v", used)
	}
	newer := writeTranscript(t, home, "beta", "/w/a", "s2", `{"type":"user","message":{"content":"hi"}}`) // another account, no turn yet
	later := time.Now().Add(time.Minute)
	if err := os.Chtimes(newer, later, later); err != nil {
		t.Fatal(err)
	}
	if snap := PullSnapshot(home, "a1", "/w/a", "", 50); snap["account"] != "beta" || snap["ctx_pct"] != 0 {
		t.Fatalf("the newest session, which has answered nothing, reads 0: %v", snap)
	}
	if snap := PullSnapshot(home, "a1", "/w/a", "s1", 50); snap["session_id"] != "s1" {
		t.Fatalf("a named session is that one: %v", snap)
	}
	if PullSnapshot(home, "a1", "/w/a", "nope", 50) != nil || PullSnapshot(home, "a1", "/w/b", "", 50) != nil {
		t.Fatal("no transcript, no snapshot")
	}
	if !CollectSnapshot(home, "a1", "/w/a", "s1") {
		t.Fatal("not written")
	}
	if b, err := os.ReadFile(paths.AgentStatusFile(home, "a1")); err != nil || !json.Valid(b) {
		t.Fatalf("%s %v", b, err)
	}
	if ContextWindow("claude-haiku-4-5-20251001") != 200_000 || ContextWindow("gpt-5") != 0 {
		t.Fatal("window by prefix, none for an unknown model")
	}
}
