package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/sessiontoken"
)

func sandboxOf(t *testing.T, shared string, c Confinement) (fs map[string]any, sandbox map[string]any, deny []any) {
	t.Helper()
	got := SandboxSettings([]byte(shared), c)
	b, _ := json.Marshal(got)
	var out struct {
		Sandbox     map[string]any
		Permissions struct{ Deny []any }
	}
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	fs, _ = out.Sandbox["filesystem"].(map[string]any)
	return fs, out.Sandbox, out.Permissions.Deny
}

func TestTheSandboxSettingsCarryTheSharedSandboxAndTheHideListsAsATerminalAgentGetsThem(t *testing.T) {
	shared := `{"sandbox":{"enabled":true,"autoAllowBashIfSandboxed":true,"network":{"allowedDomains":["github.com"]},
		"filesystem":{"allowWrite":["/h"],"denyWrite":["/backups"]}},"permissions":{"deny":["Read(/etc/x)"],"allow":["Bash(cat:*)"]}}`
	fs, sb, deny := sandboxOf(t, shared, Confinement{Hide: []string{"/h/agents/bob/memory"}, NoWrite: []string{"/h/team.yaml"}, ExtraDirs: []string{"/w2"}})
	if sb["enabled"] != true || sb["autoAllowBashIfSandboxed"] != true || sb["network"] == nil {
		t.Fatalf("the shared sandbox is carried: %v", sb)
	}
	for key, want := range map[string][]any{"denyRead": {"/h/agents/bob/memory"}, "denyWrite": {"/backups", "/h/team.yaml"}, "allowWrite": {"/h", "/w2"}} {
		if !slices.Equal(fs[key].([]any), want) {
			t.Errorf("%s: %v, want %v", key, fs[key], want)
		}
	}
	if !slices.Equal(deny, []any{"Read(/etc/x)", "Read(//h/agents/bob/memory/**)", "Edit(//h/team.yaml)"}) {
		t.Fatalf("the file tools are denied too: %v", deny)
	}
	for _, shut := range []string{`{}`, `not json`} {
		if _, sb, _ := sandboxOf(t, shut, Confinement{}); sb["enabled"] != true {
			t.Fatalf("settings %q: the sandbox is on whatever the shared settings say", shut)
		}
	}
	if _, sb, deny := sandboxOf(t, shared, Confinement{SandboxOff: true, Hide: []string{"/x"}}); sb["enabled"] != false || len(deny) != 2 {
		t.Fatalf("the founder's choice turns the sandbox off and keeps the file-tool denies: %v %v", sb, deny)
	}
}

// What the CLI is started with is what a terminal session of the agent would be kept out of.
func TestAStructuredSessionHidesTheOtherAgentsWithTheSandboxOffByDefault(t *testing.T) {
	p, _, _, _ := wired(t)
	home := p.home
	os.MkdirAll(filepath.Join(home, "agents", "bob", "memory"), 0o755)
	if err := p.Run.Open(lifecycle.StartSpec{Agent: "alice", Worktree: t.TempDir(), SessionID: "33333333-3333-4333-8333-333333333333"}); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(filepath.Join(home, "structured", "alice", "settings.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Sandbox struct {
			Enabled    bool
			Filesystem struct{ DenyRead, DenyWrite []string }
		}
		Permissions struct{ Deny []string }
	}
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatal(err)
	}
	bobMemory := filepath.Join(home, "agents", "bob", "memory")
	if got.Sandbox.Enabled || !slices.Contains(got.Sandbox.Filesystem.DenyRead, bobMemory) ||
		!slices.Contains(got.Permissions.Deny, "Read(/"+bobMemory+"/**)") {
		t.Fatalf("another agent's memory is not hidden: %s", b)
	}
	for _, folder := range sessiontoken.New(home).HiddenFolders() { // the hooks' keys and the founder's
		if !slices.Contains(got.Sandbox.Filesystem.DenyRead, folder) || !slices.Contains(got.Permissions.Deny, "Read(/"+folder+"/**)") {
			t.Fatalf("the key folder %s is not hidden: %s", folder, b)
		}
	}
	team := filepath.Join(home, "team.yaml")
	if !slices.Contains(got.Sandbox.Filesystem.DenyWrite, team) || !slices.Contains(got.Permissions.Deny, "Edit(/"+team+")") {
		t.Fatalf("the roster is writable: %s", b)
	}
}
