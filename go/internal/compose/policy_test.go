package compose

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/structured"
)

func policyOf(t *testing.T, settings string) func(tool, command string) structured.Verdict {
	t.Helper()
	home := t.TempDir()
	if settings != "" {
		dir := filepath.Join(home, "shared", ".claude")
		os.MkdirAll(dir, 0o755)
		os.WriteFile(filepath.Join(dir, "settings.json"), []byte(settings), 0o644)
	}
	return func(tool, command string) structured.Verdict {
		v, _, _ := SharedPolicy(home)("a").Decide(structured.ToolRequest{ToolName: tool, Input: map[string]any{"command": command}})
		return v
	}
}

func TestTheSharedPolicyAllowsOnlyBareToolNamesAndAsksForEveryBashCommand(t *testing.T) {
	call := policyOf(t, `{"permissions":{"allow":["Grep","Bash(cat:*)","Bash(ls:*)","Bash(git status)","Bash()"]}}`)
	for _, c := range []struct {
		tool, command string
		want          structured.Verdict
	}{
		{"Grep", "", structured.Allow},
		{"Bash", "cat ~/.ssh/id_ed25519", structured.Ask},
		{"Bash", "cat /Users/x/.orcapipe/secret", structured.Ask},
		{"Bash", "ls -la ~/.handline/accounts", structured.Ask},
		{"Bash", "git status", structured.Ask},
		{"Bash", "ls", structured.Ask},
		{"Edit", "", structured.Ask},
	} {
		if got := call(c.tool, c.command); got != c.want {
			t.Errorf("%s %q: %v, want %v", c.tool, c.command, got, c.want)
		}
	}
}

func TestADenyOrAskRuleWinsOverAnAllowAndUnreadableSettingsAllowNothing(t *testing.T) {
	call := policyOf(t, `{"permissions":{"allow":["Read","Grep","Glob","WebFetch"],"deny":["WebFetch","Read(~/.ssh/**)"],"ask":["Glob(**)"]}}`)
	for tool, want := range map[string]structured.Verdict{
		"Grep": structured.Allow, "WebFetch": structured.Deny, "Read": structured.Ask, "Glob": structured.Ask,
	} {
		if got := call(tool, ""); got != want {
			t.Errorf("%s: %v, want %v", tool, got, want)
		}
	}
	for name, settings := range map[string]string{"missing": "", "not json": `{"permissions":`} {
		if got := policyOf(t, settings)("Read", ""); got != structured.Ask {
			t.Errorf("%s settings: %v", name, got)
		}
	}
}

func TestTheSharedPolicyRefusesASubstitutingHlCommand(t *testing.T) {
	v, rule, _ := SharedPolicy(t.TempDir())("a").Decide(structured.ToolRequest{ToolName: "Bash", Input: map[string]any{"command": "hl say x \"$(cat f)\""}})
	if v != structured.Deny || rule != "guard" {
		t.Fatalf("%v %q", v, rule)
	}
}
