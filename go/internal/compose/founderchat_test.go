package compose

import (
	"os"
	"strings"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/turns"
)

// The founder's words are typed only where nothing else takes them: not while a person or device holds the input, not at a shell.
func TestTheFoundersWordsAreNotTypedAtAHeldTerminalOrAShell(t *testing.T) {
	tr := &turns.Tracker{Agent: func(string) string { return "alice" }}
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

// While the session's process is watched its hooks say it runs, so an exit banner left on its screen does not stop the typing; the screen
// is read only when the hooks do not know.
func TestTheHooksNotTheScreenSayASessionRuns(t *testing.T) {
	for _, watched := range []bool{true, false} {
		tr := &turns.Tracker{Agent: func(string) string { return "alice" }}
		if watched {
			tr.OnEnd(func(string) int { return os.Getpid() }, nil)
		}
		p, _ := rigWith(t, func(c *Config) { c.Turns = tr })
		ft := newFakeTerminals()
		ft.screen = []string{strings.Repeat("─", 40), "│ > │", strings.Repeat("─", 40), "  ? for shortcuts", "Resume this session with claude --resume x"}
		p.LC = &Lifecycle{term: ft.term()}
		dbpkg.SetTerminal(p.db, "alice", "%1", t.TempDir())
		tr.Hook("alice", "Stop", map[string]any{}, time.Now().Unix())
		if why := p.typeable("alice"); (why == "") != watched {
			t.Fatalf("watched %v: %q", watched, why)
		}
	}
}
