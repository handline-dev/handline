package session

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/store"
)

// guardCase is one case of tests/fixtures/hook_guards.json: what the Python PreToolUse hook decided on a tool call in a
// world it describes (tests/support.py HOOK_GUARD_CASES says what each field means).
type guardCase struct {
	Name     string          `json:"name"`
	Agent    string          `json:"agent"`
	Tool     string          `json:"tool"`
	Input    json.RawMessage `json:"input"`
	Index    *string         `json:"index"`
	Stale    []string        `json:"stale"`
	Fresh    []string        `json:"fresh"`
	NoteLink bool            `json:"note_link"`
	GateOff  bool            `json:"gate_off"`
	Current  json.RawMessage `json:"current"`
	Tasks    []struct {
		Title    string `json:"title"`
		Priority int    `json:"priority"`
		Owner    string `json:"owner"`
		Done     bool   `json:"done"`
	} `json:"tasks"`
	Deny *string `json:"deny"`
}

func TestPreToolUseDecidesWhatThePythonHookDecided(t *testing.T) {
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "hook_guards.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct{ Cases []guardCase }
	if err := json.Unmarshal(b, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Cases) < 30 {
		t.Fatalf("only %d cases", len(fixture.Cases))
	}
	root := t.TempDir()
	t.Setenv("HANDLINE_TEST_ROOT", root)
	t.Setenv("HANDLINE_AGENT", "") // the founder's terminal, which may switch the edit gate off
	t.Setenv("OCP_AGENT", "")
	for _, c := range fixture.Cases {
		t.Run(c.Name, func(t *testing.T) {
			want := ""
			if c.Deny != nil {
				want = *c.Deny
			}
			if got := decide(t, root, c); got != want {
				t.Errorf("\n got  %q\n want %q", got, want)
			}
		})
	}
}

// decide builds the case's world under root and runs the Go guards on its call.
func decide(t *testing.T, root string, c guardCase) string {
	agent := c.Agent
	if agent == "" {
		agent = "a.one"
	}
	home := filepath.Join(root, c.Name)
	tmp, cfg := filepath.Join(root, c.Name+".tmp"), filepath.Join(root, c.Name+".cfg")
	t.Setenv("TMPDIR", tmp)
	t.Setenv("CLAUDE_CONFIG_DIR", cfg)
	mem, note := memoryDir(home, agent), paths.HandoffNoteFile(home, agent)
	write := func(p, text string) {
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.MkdirAll(mem, 0o755); err != nil {
		t.Fatal(err)
	}
	if c.Index != nil {
		write(filepath.Join(mem, memoryIndex), *c.Index)
	}
	for _, rel := range append(c.Stale, c.Fresh...) {
		write(filepath.Join(mem, rel), "x")
	}
	for _, rel := range c.Stale {
		then := time.Now().Add(-40 * 24 * time.Hour)
		if err := os.Chtimes(filepath.Join(mem, rel), then, then); err != nil {
			t.Fatal(err)
		}
	}
	if c.NoteLink {
		write(filepath.Join(home, "elsewhere.md"), "x")
		if err := os.Symlink(filepath.Join(home, "elsewhere.md"), note); err != nil {
			t.Fatal(err)
		}
	}
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	addTask := func(title, owner string, priority int) int64 {
		res, err := d.Exec("INSERT INTO tasks(title, owner, opened_by, created_at, priority) VALUES(?, ?, 'x', 0, ?)", title, owner, priority)
		if err != nil {
			t.Fatal(err)
		}
		id, _ := res.LastInsertId()
		return id
	}
	for _, task := range c.Tasks {
		owner := task.Owner
		if owner == "" {
			owner = agent
		}
		id := addTask(task.Title, owner, task.Priority)
		if task.Done {
			if _, err := d.Exec("UPDATE tasks SET done_evidence = 'abc' WHERE id = ?", id); err != nil {
				t.Fatal(err)
			}
		}
	}
	if c.Current == nil { // absent: the agent holds a current task
		id := addTask("held", agent, 2)
		if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated, current_task) VALUES(?, 'IDLE', 0, ?)", agent, id); err != nil {
			t.Fatal(err)
		}
	}
	if c.GateOff {
		if err := protected.New(home).Put("gates", "edit_task", "off"); err != nil {
			t.Fatal(err)
		}
	}
	input := strings.NewReplacer("{memory}", mem, "{tmp}", tmp, "{plans}", cfg+"/plans", "{note}", note,
		"{repo}", filepath.Join(home, "repo")).Replace(string(c.Input))
	var toolInput any
	if err := json.Unmarshal([]byte(input), &toolInput); err != nil {
		t.Fatal(err)
	}
	return PreToolUse(d, home, agent, c.Tool, toolInput)
}
