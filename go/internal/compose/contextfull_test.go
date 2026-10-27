package compose

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/handoff"
)

// A closed session is full when its last reply held at least the agent's context limit; an unreadable session, a limit of 0 or no
// limit at all is never full.
func TestAClosedSessionIsFullAtTheAgentsContextLimit(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, "accounts", "alpha", ".claude", "projects", "-proj")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	reply := `{"type":"assistant","message":{"model":"m","usage":{"input_tokens":60,"cache_read_input_tokens":30,"cache_creation_input_tokens":10}}}` + "\n"
	if err := os.WriteFile(filepath.Join(dir, "sess-1.jsonl"), []byte(reply), 0o644); err != nil { // 100 tokens
		t.Fatal(err)
	}
	at := func(limit int) func(string, string) int { return func(string, string) int { return limit } }
	for _, c := range []struct {
		name    string
		session string
		limit   func(string, string) int
		want    bool
	}{
		{"at the limit", "sess-1", at(100), true},
		{"over the limit", "sess-1", at(99), true},
		{"under the limit", "sess-1", at(101), false},
		{"a limit of 0", "sess-1", at(0), false},
		{"no limit", "sess-1", nil, false},
		{"no transcript", "sess-2", at(1), false},
	} {
		h := &handoffPort{env: &handoff.Env{Home: home}, limit: c.limit}
		if got := h.ContextFull(nil, "a", c.session); got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, got, c.want)
		}
	}
}
