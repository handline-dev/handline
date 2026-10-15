package compose

import (
	"encoding/json"
	"github.com/handline/handline/go/internal/team"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/sessiontoken"
	"github.com/handline/handline/go/internal/yamlcompat"
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
	c := Confinement{ExtraDirs: []string{"/w2"}, AllowWrite: []string{"/r/.git"}, AllowDomains: []string{"registry.npmjs.org"}, CacheDir: "/h/caches/a"}
	fs, sb, _ = sandboxOf(t, shared, c)
	if !slices.Equal(fs["allowWrite"].([]any), []any{"/h", "/w2", "/r/.git", "/h/caches/a"}) {
		t.Fatalf("the allowances are written beside the worktrees: %v", fs["allowWrite"])
	}
	if env := SandboxSettings([]byte(shared), c)["env"].(map[string]any); env["GOCACHE"] != "/h/caches/a/go-build" || env["npm_config_store_dir"] != "/h/caches/a/pnpm-store" {
		t.Fatalf("the caches are the session's own: %v", env)
	}
	if _, ok := SandboxSettings([]byte(shared), Confinement{SandboxOff: true})["env"]; ok {
		t.Fatal("an unsandboxed session keeps the shared caches")
	}
	network := sb["network"].(map[string]any)
	if !slices.Equal(network["allowedDomains"].([]any), []any{"github.com", "registry.npmjs.org"}) {
		t.Fatalf("the hosts are added to the shared ones: %v", network)
	}
	if _, ok := sb["enableWeakerNetworkIsolation"]; ok || sb["excludedCommands"] != nil {
		t.Fatalf("a repo the founder allowed nothing keeps the full isolation and runs every command inside: %v", sb)
	}
	c.Allowed = team.RepoAllowances{WeakerNetwork: true, Excluded: []string{"pnpm install"}}
	_, sb, _ = sandboxOf(t, `{"sandbox":{"excludedCommands":["xcrun *"]}}`, c)
	if sb["enableWeakerNetworkIsolation"] != true || !slices.Equal(sb["excludedCommands"].([]any), []any{"xcrun *", "pnpm install"}) {
		t.Fatalf("the repo's allowances, its commands added to the shared ones: %v", sb)
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
	for _, folder := range sessiontoken.New(home).HiddenFolders() { // the founder's key
		if !slices.Contains(got.Sandbox.Filesystem.DenyRead, folder) || !slices.Contains(got.Permissions.Deny, "Read(/"+folder+"/**)") {
			t.Fatalf("the key folder %s is not hidden: %s", folder, b)
		}
	}
	team := filepath.Join(home, "team.yaml")
	if !slices.Contains(got.Sandbox.Filesystem.DenyWrite, team) || !slices.Contains(got.Permissions.Deny, "Edit(/"+team+")") {
		t.Fatalf("the roster is writable: %s", b)
	}
}

// The CLI's pid is recorded as the agent's session while it runs, as a terminal launch records its own: the daemon tells the agent's hl
// by it, and without it would fall through to the founder key.
func TestAStructuredSessionIsRecordedAsTheAgentsWhileItRuns(t *testing.T) {
	p, lc, _, _ := wired(t)
	tokens := lc.Launch.Tokens
	if err := p.Run.Open(lifecycle.StartSpec{Agent: "alice", Worktree: t.TempDir(), SessionID: "44444444-4444-4444-8444-444444444444"}); err != nil {
		t.Fatal(err)
	}
	s := p.Run.mgr.Get("alice")
	pid := s.Pid()
	if got := tokens.AgentOf(pid); got != "alice" {
		t.Fatalf("the CLI (pid %d) is told as %q", pid, got)
	}
	if err := p.Run.Close("alice"); err != nil {
		t.Fatal(err)
	}
	<-s.Exited()
	if kept, _ := tokens.Store.Get(sessiontoken.NS, "alice"); strings.Contains(kept, strconv.Itoa(pid)+" ") {
		t.Fatalf("the record outlives the session: %q", kept)
	}
}

func TestAStructuredSessionCarriesTheAllowancesOfItsRepoOnly(t *testing.T) {
	p, lc, _, wt := wired(t)
	if err := lc.roster.Store.Put(team.RepoAllowanceNS, "orcapipe", "weaker-network\npnpm-install"); err != nil {
		t.Fatal(err)
	}
	for repo, want := range map[string]bool{"/ws/orcapipe": true, "/ws/harbor-app": false} {
		if err := lc.roster.SetKeys("alice", []yamlcompat.Pair{{Key: "sandbox", Val: true}, {Key: "repo", Val: repo}}); err != nil {
			t.Fatal(err)
		}
		c, err := p.Run.confinementOf("alice", wt)
		if err != nil {
			t.Fatal(err)
		}
		if c.Allowed.WeakerNetwork != want || (len(c.Allowed.Excluded) > 0) != want {
			t.Errorf("%s: %+v", repo, c.Allowed)
		}
	}
}
