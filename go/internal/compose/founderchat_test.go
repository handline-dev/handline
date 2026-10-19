package compose

import (
	"strings"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/turns"
)

// The founder's words are typed only where nothing else takes them: not while a person or device holds the input, not at a shell.
func TestTheFoundersWordsAreNotTypedAtAHeldTerminalOrAShell(t *testing.T) {
	tr := &turns.Tracker{}
	p, _ := rigWith(t, func(c *Config) { c.Turns = tr })
	ft := newFakeTerminals()
	idle := []string{strings.Repeat("─", 40), "│ > │", strings.Repeat("─", 40), "  ? for shortcuts"}
	ft.screen = idle
	p.LC = &Lifecycle{term: ft.term()}
	dbpkg.SetTerminal(p.db, "alice", "%1", t.TempDir())
	tr.Hook("alice", "UserPromptSubmit", map[string]any{}, time.Now().Unix())
	tr.Hook("alice", "Stop", map[string]any{}, time.Now().Unix())
	if why := p.typeable("alice"); why != "" {
		t.Fatalf("an idle session: %q", why)
	}
	now := time.Now().Unix()
	p.db.Exec("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES('alice', 'person', 'phone', ?, ?)", now, now)
	if why := p.typeable("alice"); !strings.Contains(why, "holds its input") {
		t.Fatalf("a held terminal: %q", why)
	}
	p.db.Exec("DELETE FROM input_holders")
	ft.screen = []string{"~/work $ "}
	if why := p.typeable("alice"); why == "" {
		t.Fatal("a shell prompt is typed at")
	}
}
