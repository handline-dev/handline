package compose

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/structured"
)

func TestTheSharedPolicyAllowsWhatTheSharedSettingsAllowAndGuardsTheShell(t *testing.T) {
	home := t.TempDir()
	policy := SharedPolicy(home)
	call := func(tool, command string) structured.Verdict {
		v, _, _ := policy("a").Decide(structured.ToolRequest{ToolName: tool, Input: map[string]any{"command": command}})
		return v
	}
	if call("Bash", "ls") != structured.Ask {
		t.Fatal("with no settings a person is asked")
	}
	dir := filepath.Join(home, "shared", ".claude")
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, "settings.json"), []byte(`{"permissions":{"allow":["Read","Bash(ls:*)","Bash(git status)"]}}`), 0o644)
	for _, c := range []struct {
		tool, command string
		want          structured.Verdict
	}{
		{"Read", "", structured.Allow},
		{"Bash", "ls", structured.Allow},
		{"Bash", "ls -la /tmp", structured.Allow},
		{"Bash", "git status", structured.Allow},
		{"Bash", "git status --short", structured.Ask},
		{"Bash", "lsof", structured.Ask},
		{"Bash", "ls; rm -rf x", structured.Ask},
		{"Bash", "ls && rm x", structured.Ask},
		{"Bash", "ls $(rm x)", structured.Ask},
		{"Bash", "ls > out", structured.Ask},
		{"Edit", "", structured.Ask},
		{"Bash", "hl say x $(whoami)", structured.Ask},
	} {
		if got := call(c.tool, c.command); got != c.want {
			t.Errorf("%s %q: %v, want %v", c.tool, c.command, got, c.want)
		}
	}
}

func TestTheSharedPolicyRefusesASubstitutingHlCommand(t *testing.T) {
	v, rule, _ := SharedPolicy(t.TempDir())("a").Decide(structured.ToolRequest{ToolName: "Bash", Input: map[string]any{"command": "hl say x \"$(cat f)\""}})
	if v != structured.Deny || rule != "guard" {
		t.Fatalf("%v %q", v, rule)
	}
}
