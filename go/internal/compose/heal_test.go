package compose

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/turns"
)

// With the hooks on, heal takes a person's need from the needs-you card and a settled turn, not the screen; with them off, the screen.
func TestHealAsksTheHooksBeforeTheScreenWhetherAPersonIsNeeded(t *testing.T) {
	rule := strings.Repeat("─", 40)
	idle := []string{rule, "│ > │", rule, "  ? for shortcuts"}
	prompt := []string{"earlier output", rule, "   Bash command", "     rm -rf build", "   Do you want to proceed?", "   ❯ 1. Yes", "     2. No",
		"   Esc to cancel · Tab to amend"}
	for _, on := range []bool{true, false} {
		tr := &turns.Tracker{Agent: func(string) string { return "alice" }}
		p, home := rigWith(t, func(c *Config) { c.Turns = tr })
		ft := newFakeTerminals()
		tm := ft.term()
		if on {
			tm.Turns = tr
		}
		p.LC = &Lifecycle{term: tm, Manager: &lifecycle.Manager{Q: p.db}}
		h := healPorts{lc: p.LC, home: home, needs: p.needsYou()}
		dbpkg.SetTerminal(p.db, "alice", "%1", t.TempDir())
		dbpkg.SetAgentState(p.db, "alice", states.Running, 0, "")

		// A tool's prompt the PermissionRequest hook wrote down, on a screen that shows nothing.
		ft.screen = idle
		os.MkdirAll(filepath.Join(home, "waiting"), 0o755)
		os.WriteFile(filepath.Join(home, "waiting", "alice.json"), []byte(`{"tool": "Bash", "command": "rm -rf build", "since": 1700}`), 0o644)
		if got := h.NeedsPerson("alice"); got != on {
			t.Fatalf("hooks %v: a hook's open prompt needs a person: %v", on, got)
		}
		os.Remove(filepath.Join(home, "waiting", "alice.json"))

		// A prompt left drawn on a screen whose turn a Stop closed since.
		ft.screen = prompt
		tr.Hook("alice", "UserPromptSubmit", map[string]any{}, time.Now().Unix())
		tr.Hook("alice", "Stop", map[string]any{}, time.Now().Unix())
		if got := h.NeedsPerson("alice"); got == on {
			t.Fatalf("hooks %v: a settled turn's screen needs a person: %v", on, got)
		}
		// No Stop since the prompt: the screen is read, for the CLI's own dialogs.
		tr.Hook("alice", "UserPromptSubmit", map[string]any{}, time.Now().Unix()+1)
		if !h.NeedsPerson("alice") {
			t.Fatalf("hooks %v: a dialog on an unsettled turn needs nobody", on)
		}
	}
}
