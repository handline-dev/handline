package session

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// guardCase is one case of tests/fixtures/hook_guards.json: a tool call and the PreToolUse hook's refusal of it, absent when it lets
// the call through.
type guardCase struct {
	Name  string          `json:"name"`
	Tool  string          `json:"tool"`
	Input json.RawMessage `json:"input"`
	Deny  *string         `json:"deny"`
}

func TestTheHookGuardDecidesAsTheFixtureSays(t *testing.T) {
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "hook_guards.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct{ Cases []guardCase }
	if err := json.Unmarshal(b, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Cases) == 0 {
		t.Fatal("no cases")
	}
	mem := t.TempDir()
	for _, c := range fixture.Cases {
		t.Run(c.Name, func(t *testing.T) {
			want := ""
			if c.Deny != nil {
				want = *c.Deny
			}
			var toolInput any
			if err := json.Unmarshal([]byte(strings.ReplaceAll(string(c.Input), "{memory}", mem)), &toolInput); err != nil {
				t.Fatal(err)
			}
			if got := Guard(c.Tool, toolInput); got != want {
				t.Errorf("\n got  %q\n want %q", got, want)
			}
		})
	}
}
