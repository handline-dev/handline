package team

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/yamlcompat"
)

func asAgent(t *testing.T, name string) {
	t.Setenv("OCP_AGENT", name)
	t.Setenv("HANDLINE_AGENT", name)
}

func TestRegisterWritesSealsAndReadsBack(t *testing.T) {
	r := NewRoster(newHome(t))
	opts := RegisterOpts{Role: "Does the work.", Repo: "", Model: strp("opus"), Effort: strp("high")}
	d, err := r.Register("worker", opts)
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range []string{"inbox", "journal", "memory", "agent.yaml", "ROLE.md", "base.md"} {
		if _, err := os.Stat(filepath.Join(d, f)); err != nil {
			t.Errorf("%s: %v", f, err)
		}
	}
	b, _ := os.ReadFile(filepath.Join(d, "agent.yaml"))
	if string(b) != "name: worker\nmodel: opus\neffort: high\nweight: light\nenabled: true\n" {
		t.Fatalf("%q", b)
	}
	if role, _ := os.ReadFile(filepath.Join(d, "ROLE.md")); string(role) != "# worker\n\nDoes the work.\n" {
		t.Fatalf("%q", role)
	}
	cfg, err := r.Read("worker")
	if err != nil {
		t.Fatal(err)
	}
	if v, m, e := Parts(cfg); v != "claude" || m != "opus" || e != "high" || Kind(cfg) != "opus high" {
		t.Fatalf("%v %v %v %s", v, m, e, Kind(cfg))
	}
	// what an agent writes into the file is refused on the next read
	os.WriteFile(filepath.Join(d, "agent.yaml"), append(b, "chrome: true\n"...), 0o644)
	if _, err := r.Read("worker"); err == nil || !strings.Contains(err.Error(), "changed outside the founder's path") {
		t.Fatalf("%v", err)
	}
	if _, err := r.Read("nobody"); err == nil {
		t.Fatal("an agent that is not registered was read")
	}
}

func TestRegisterAgainChangesOnlyWhatWasAskedAndKeepsOrder(t *testing.T) {
	r := NewRoster(newHome(t))
	r.Register("worker", RegisterOpts{Model: strp("sonnet"), Repo: ""})
	on := true
	if _, err := r.Register("worker", RegisterOpts{Effort: strp("max"), Chrome: &on, ExtraWorktrees: i64(5)}); err == nil {
		t.Fatal("extra_worktrees past the most was taken")
	}
	if _, err := r.Register("worker", RegisterOpts{Effort: strp("max"), Chrome: &on, ExtraWorktrees: i64(2), Weight: "heavy"}); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(r.AgentFile("worker"))
	if string(b) != "name: worker\nmodel: sonnet\neffort: max\nweight: heavy\nenabled: true\nextra_worktrees: 2\nchrome: true\n" {
		t.Fatalf("%q", b)
	}
	cfg, err := r.Read("worker")
	if err != nil || !Chrome(cfg) || ExtraWorktrees(cfg) != 2 {
		t.Fatalf("%v %v", cfg, err)
	}
	off := false
	if _, err := r.Register("worker", RegisterOpts{Chrome: &off, ExtraWorktrees: i64(0)}); err != nil {
		t.Fatal(err)
	}
	b, _ = os.ReadFile(r.AgentFile("worker"))
	if string(b) != "name: worker\nmodel: sonnet\neffort: max\nweight: heavy\nenabled: true\n" {
		t.Fatalf("%q", b)
	}
}

func i64(n int64) *int64 { return &n }

func TestOnlyTheFoundersSessionRegistersAndGrants(t *testing.T) {
	r := NewRoster(newHome(t))
	asAgent(t, "worker")
	on := true
	if _, err := r.Register("evil", RegisterOpts{Sandbox: &on}); err == nil {
		t.Fatal("an agent registered")
	}
	if _, err := os.Stat(r.AgentFile("evil")); err == nil {
		t.Fatal("a refusal left a file behind")
	}
	asAgent(t, "")
	if _, err := r.Register("worker", RegisterOpts{Mode: strp("bypass")}); err == nil || !strings.Contains(err.Error(), "mode must be one of") {
		t.Fatalf("bypass was taken as a mode: %v", err)
	}
	if _, err := r.Register("worker", RegisterOpts{Mode: strp("plan")}); err != nil {
		t.Fatal(err)
	}
	cfg, _ := r.Read("worker")
	if r.ModeOf("worker", cfg) != "plan" {
		t.Fatalf("%v", cfg)
	}
	// a file from before bypass was removed runs in the default mode
	if r.ModeOf("worker", Config{"mode": "bypass", "skip_permissions": true}) != DefaultMode {
		t.Fatal("an old bypass file was taken as more than the default")
	}
	if r.SandboxOn("worker", cfg) {
		t.Fatal("before the founder's switch the sandbox is off unless the file turns it on")
	}
	on, off := true, false
	r.Register("worker", RegisterOpts{Sandbox: &on})
	cfg, _ = r.Read("worker")
	if !r.SandboxOn("worker", cfg) || cfg["sandbox"] != true {
		t.Fatalf("sandbox on is sandbox: true in the file: %v", cfg)
	}
	r.Register("worker", RegisterOpts{Sandbox: &off})
	cfg, _ = r.Read("worker")
	if cfg["sandbox"] != false || r.SandboxOn("worker", cfg) {
		t.Fatalf("sandbox off is sandbox: false in the file, so the founder's default does not turn it on: %v", cfg)
	}
}

func TestTheFoundersSwitchTurnsTheSandboxOnWhereAgentYamlSaysNothing(t *testing.T) {
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_AGENT", "")
	r := NewRoster(newHome(t))
	if err := r.Store.Put("switches", "sandbox_default", "on"); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		cfg  Config
		want bool
	}{{Config{}, true}, {Config{"sandbox": false}, false}, {Config{"sandbox": true}, true}, {Config{"sandbox": "no"}, true}} {
		if got := r.SandboxOn("worker", c.cfg); got != c.want {
			t.Fatalf("%v: on %v, want %v", c.cfg, got, c.want)
		}
	}
	if err := r.Store.Put("switches", "sandbox_default", "off"); err != nil {
		t.Fatal(err)
	}
	if r.SandboxOn("worker", Config{}) {
		t.Fatal("the switch off leaves the old default")
	}
	got := SandboxDomains(Config{"sandbox_domains": " expo.dev,, "})
	if strings.Join(got, ",") != "registry.npmjs.org,proxy.golang.org,sum.golang.org,expo.dev" {
		t.Fatalf("the registries and the strings agent.yaml adds: %v", got)
	}
}

func TestARepoHasOnlyTheSandboxAllowancesTheFounderGaveIt(t *testing.T) {
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_AGENT", "")
	r := NewRoster(newHome(t))
	ours, theirs := Config{"repo": "/ws/orcapipe/"}, Config{"repo": "/ws/harbor-app"}
	if got := r.RepoAllowancesOf(ours); got.WeakerNetwork || got.Excluded != nil {
		t.Fatalf("a repo has none until the founder gives them: %+v", got)
	}
	if err := r.Store.Put(RepoAllowanceNS, "orcapipe", "weaker-network\npnpm-install\nrm-rf"); err != nil {
		t.Fatal(err)
	}
	if got := r.RepoAllowancesOf(ours); !got.WeakerNetwork || !slices.Equal(got.Excluded, []string{"pnpm install", "pnpm install --frozen-lockfile"}) {
		t.Fatalf("the repo's entry, and a line that names no allowance grants nothing: %+v", got)
	}
	// The CLI takes a pattern as the whole command, or as its start when it ends in " *": an add of a package stays in the sandbox.
	for _, pattern := range r.RepoAllowancesOf(ours).Excluded {
		start, prefix := strings.CutSuffix(pattern, " *")
		if cmd := "pnpm install left-pad"; cmd == pattern || prefix && strings.HasPrefix(cmd, start+" ") {
			t.Fatalf("%q runs %q outside the sandbox", pattern, cmd)
		}
	}
	for _, cfg := range []Config{theirs, {}, {"repo": "/ws/@personal"}} {
		if got := r.RepoAllowancesOf(cfg); got.WeakerNetwork || got.Excluded != nil {
			t.Fatalf("%v: another repo has none: %+v", cfg, got)
		}
	}
}

func TestRegisterRefusesWhatCouldNotBeStored(t *testing.T) {
	r := NewRoster(newHome(t))
	for _, name := range []string{"", "a/b", ".hidden", "a\nb", "on", "12", "null"} {
		if _, err := r.Register(name, RegisterOpts{}); err == nil {
			t.Errorf("%q was registered", name)
		}
	}
	if _, err := r.Register("w", RegisterOpts{Effort: strp("huge")}); err == nil || !strings.Contains(err.Error(), "effort must be one of") {
		t.Fatalf("%v", err)
	}
	if _, err := r.Register("w", RegisterOpts{Vendor: strp("nope")}); err == nil {
		t.Fatal("a vendor with no driver")
	}
	if _, err := r.Register("w", RegisterOpts{Mode: strp("wild")}); err == nil {
		t.Fatal("a mode that does not exist")
	}
	if _, err := os.Stat(r.AgentFile("w")); err == nil {
		t.Fatal("a refusal left an agent behind")
	}
}

func TestReadRefusesAControlCharacterAndANonMap(t *testing.T) {
	r := NewRoster(newHome(t))
	r.Register("worker", RegisterOpts{})
	write := func(text string) {
		os.WriteFile(r.AgentFile("worker"), []byte(text), 0o644)
		r.Seals.SealEverything()
	}
	write("name: worker\nrepo: \"/a\x1b[31mb\"\n")
	if _, err := r.Read("worker"); err == nil || !strings.Contains(err.Error(), "control character") {
		t.Fatalf("%v", err)
	}
	write("- a\n- b\n")
	if _, err := r.Read("worker"); err == nil || !strings.Contains(err.Error(), "top level is not a map") {
		t.Fatalf("%v", err)
	}
	var ce *yamlcompat.ConfigError
	_ = ce
}

func TestConfigReaders(t *testing.T) {
	if ContextTokensOf(Config{}) != ContextTokens || ContextTokensOf(Config{"context_tokens": int64(50000)}) != 50000 ||
		ContextTokensOf(Config{"context_tokens": true}) != ContextTokens || ContextTokensOf(Config{"context_tokens": int64(-1)}) != ContextTokens ||
		ContextTokensOf(Config{"context_tokens": "9"}) != ContextTokens {
		t.Error("context_tokens")
	}
	if ContextTokens != 300_000 {
		t.Errorf("the default context-handoff limit is 300,000 tokens, not %d", ContextTokens)
	}
	if ExtraWorktrees(Config{"extra_worktrees": int64(9)}) != 2 || ExtraWorktrees(Config{"extra_worktrees": true}) != 0 ||
		ExtraWorktrees(Config{"extra_worktrees": int64(1)}) != 1 {
		t.Error("extra_worktrees")
	}
	if v, m, e := Parts(Config{"model": int64(4), "effort": "", "vendor": nil}); v != "claude" || m != "4" || e != "medium" {
		t.Errorf("%s %s %s", v, m, e)
	}
	if Kind(Config{"vendor": "opencode", "effort": "xhigh"}) != "opencode sonnet xhi" || Kind(Config{"effort": "odd"}) != "sonnet odd" {
		t.Error("kind")
	}
	if got := MCPConfigArgs(Config{"mcp_config": `{"mcpServers": {}}`}); len(got) != 2 || got[1] != `{"mcpServers":{}}` {
		t.Errorf("%v", got)
	}
	if MCPConfigArgs(Config{"mcp_config": "garbage"}) != nil || MCPConfigArgs(Config{}) != nil {
		t.Error("a value that is not a config adds nothing")
	}
}

// human and gate are the board's own names: no agent takes one.
func TestNoAgentIsNamedHumanOrGate(t *testing.T) {
	r := NewRoster(t.TempDir())
	for _, name := range []string{"human", "gate"} {
		if _, err := r.Register(name, RegisterOpts{Role: "r"}); err == nil || !strings.Contains(err.Error(), "the board's own name") {
			t.Errorf("%s: %v", name, err)
		}
	}
}
