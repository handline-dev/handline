package team

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/yamlcompat"
)

func parseTeam(t *testing.T, text string) *Team {
	t.Helper()
	v, err := yamlcompat.ParseOrdered(text)
	if err != nil {
		t.Fatal(err)
	}
	tm, err := NewTeam(v.(*yamlcompat.OMap))
	if err != nil {
		t.Fatal(err)
	}
	return tm
}

// The expected decisions are what the Python team.route answers for the same policy.
func TestRouteAnswersAsThePythonPolicyDoes(t *testing.T) {
	type want struct {
		allowed bool
		reason  string
	}
	starter := parseTeam(t, StarterTeam)
	for _, c := range []struct {
		from, to string
		want
	}{
		{"backend", "reviewer", want{true, "edge * -> reviewer"}},
		{"reviewer", "backend", want{true, "edge reviewer -> *"}},
		{"backend", "archivist", want{true, "edge * -> archivist"}},
		{"backend", "human", want{true, "edge * -> human"}},
		{"human", "backend", want{true, "edge human -> *"}},
		{"backend", "backend", want{false, "an agent cannot ask itself"}},
		{"backend", "ghost", want{false, "unknown target 'ghost'"}},
		{"harbor.sea", "harbor", want{false, "unknown target 'harbor'"}},
		{"harbor.sea", "human", want{true, "edge * -> human"}},
	} {
		d := starter.Route(c.from, c.to, 0)
		got := want{d.Allowed, d.Reason}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s -> %s: %+v, want %+v", c.from, c.to, got, c.want)
		}
	}
	second := parseTeam(t, `teams:
  harbor: [harbor, harbor.sea, harbor.air]
  eng: [backend, front?end]
edges:
  - {from: "eng.*", to: "harbor", relation: peer, response: fire_and_forget, kind: escalate}
  - {from: "bac*", to: "human", kind: task}
deny:
  - {from: "front?end", to: "harbor", reason: "no way"}
limits:
  max_chain_depth: 2
`)
	for _, c := range []struct {
		from, to string
		depth    int
		want
	}{
		{"backend", "harbor", 0, want{true, "edge eng.* -> harbor"}},
		{"front?end", "harbor", 0, want{false, "denied: front?end -> harbor: no way"}},
		{"harbor.sea", "harbor", 2, want{false, "chain depth 2 reached the limit 2 (loop guard)"}},
		{"backend", "human", 0, want{true, "edge bac* -> human"}},
		{"harbor", "human", 0, want{true, "default link: harbor may ask the founder"}},
		{"zzz", "backend", 0, want{false, "denied: no edge declares zzz -> backend (unlisted edges are denied by default)"}},
		{"backend", "front?end", 0, want{false, "denied: no edge declares backend -> front?end (unlisted edges are denied by default)"}},
	} {
		d := second.Route(c.from, c.to, c.depth)
		got := want{d.Allowed, d.Reason}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s -> %s: %+v, want %+v", c.from, c.to, got, c.want)
		}
	}
	if d := second.Route("harbor.sea", "harbor", 0); !d.Allowed || d.Reason != "default link: harbor.sea is harbor's child" {
		t.Errorf("a child may ask its parent: %+v", d)
	}
	if got := second.TeamOf("front?end"); got != "eng" {
		t.Errorf("%q", got)
	}
}

func TestADenyOrEdgeThatCouldSilentlyAllowIsRefusedAtLoad(t *testing.T) {
	for src, want := range map[string]string{
		"deny:\n  - {from: a}\n":             "deny[0] is missing to (keys present: from)",
		"deny:\n  - plain\n":                 "deny[0] is not a map: 'plain'",
		"edges:\n  - {from: a}\n":            "edges[0] needs both 'from' and 'to'",
		"edges:\n  - {from: 'x.*', to: b}\n": "refers to team 'x', which is not in teams (none)",
		"agents:\n  a: 1\n":                  "agents.a is not a map",
	} {
		v, err := yamlcompat.ParseOrdered(src)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := NewTeam(v.(*yamlcompat.OMap)); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%q: %v, want %q", src, err, want)
		}
	}
}

// The answer-model removal drops these keys from team.yaml last, so until then a table with them routes as one without.
func TestAnEdgesRelationResponseAndKindAreIgnored(t *testing.T) {
	const roster = "teams:\n  eng: [eng, lead, dev]\nagents:\n  lead: {}\n  dev: {}\nedges:\n"
	old := parseTeam(t, roster+`  - { from: lead, to: dev, relation: peer, response: blocking }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
  - { from: dev, to: lead, relation: [odd], response: 7, kind: verdict }
`)
	bare := parseTeam(t, roster+`  - { from: lead, to: dev }
  - { from: "*", to: "human" }
  - { from: "human", to: "*" }
  - { from: dev, to: lead }
`)
	names := []string{"lead", "dev", "eng", "human"}
	for _, a := range names {
		for _, b := range names {
			if got, want := old.Route(a, b, 0), bare.Route(a, b, 0); !reflect.DeepEqual(got, want) {
				t.Errorf("%s -> %s: %+v, want %+v", a, b, got, want)
			}
		}
	}
}

func TestFnmatch(t *testing.T) {
	for _, c := range []struct {
		name, pat string
		want      bool
	}{{"backend", "back*", true}, {"backend", "b?ckend", true}, {"backend", "[a-c]ackend", true}, {"backend", "[!b]ackend", false},
		{"backend", "Back*", false}, {"a.b", "a.*", true}, {"a+b", "a+b", true}, {"ab", "a[", false}, {"a[", "a[", true}} {
		if fnmatchCase(c.name, c.pat) != c.want {
			t.Errorf("%q ~ %q", c.name, c.pat)
		}
	}
}

func TestMCPConfigTextIsOneLineOfASCIIJSONInOrder(t *testing.T) {
	got, err := MCPConfigText(`{"mcpServers": {"a": {"command": "x", "args": ["é", 1.5, true, null]}}, "other": 1}`)
	want := `{"mcpServers":{"a":{"command":"x","args":["\u00e9",1.5,true,null]}},"other":1}`
	if err != nil || got != want {
		t.Fatalf("%q\n%q %v", got, want, err)
	}
	for _, bad := range []string{`{"nope":1}`, `[1]`, `{"mcpServers": 1}`, `{`} {
		if _, err := MCPConfigText(bad); err == nil {
			t.Errorf("%q was taken", bad)
		}
	}
}

func TestBrowserConfigRoundTripsLikeThePython(t *testing.T) {
	home := "/h"
	cfg := Config{"mcp_config": `{"mcpServers":{"keep":{"command":"k"},"browser":{"command":"sh","args":["-c","x --profile-dir-name \"P9\" --allowed-origins \"https://a.com;https://b.org:8080\""]}}}`}
	if BrowserMode(cfg) != "playwright" || !reflect.DeepEqual(BrowserOrigins(cfg), []string{"https://a.com", "https://b.org:8080"}) {
		t.Fatalf("%s %v", BrowserMode(cfg), BrowserOrigins(cfg))
	}
	got, err := BrowserMCPConfig(home, cfg, "playwright", []string{"https://x.io"})
	want := fmt.Sprintf(`{"mcpServers":{"keep":{"command":"k"},"browser":{"command":"sh","args":["-c","PLAYWRIGHT_MCP_EXTENSION_TOKEN=$(cat %s/browser/token) exec %s/browser/node_modules/.bin/playwright-mcp --extension --profile-dir-name \"P9\" --allowed-origins \"https://x.io\""]}}}`, home, home)
	if err != nil || got != want {
		t.Fatalf("%q %v", got, err)
	}
	if off, _ := BrowserMCPConfig(home, cfg, "off", nil); off != `{"mcpServers":{"keep":{"command":"k"}}}` {
		t.Fatalf("%q", off)
	}
	if none, _ := BrowserMCPConfig(home, Config{}, "off", nil); none != "" {
		t.Fatalf("%q", none)
	}
	if _, err := BrowserMCPConfig(home, cfg, "custom", nil); err == nil {
		t.Fatal("a custom browser is not set here")
	}
	if BrowserMode(Config{"chrome": true}) != "chrome" || BrowserMode(Config{}) != "off" ||
		BrowserMode(Config{"mcp_config": `{"mcpServers":{"browser":{"args":["run"]}}}`}) != "custom" {
		t.Fatal("browser modes")
	}
}

func TestParseOrigins(t *testing.T) {
	got, err := ParseOrigins("https://a.com, https://b.com/ ; https://a.com")
	if err != nil || !reflect.DeepEqual(got, []string{"https://a.com", "https://b.com"}) {
		t.Fatalf("%v %v", got, err)
	}
	for _, bad := range []string{"ftp://a.com", "a.com", "https://a.com/path", "https://a.com;rm -rf"} {
		if _, err := ParseOrigins(bad); err == nil {
			t.Errorf("%q was taken", bad)
		}
	}
	if _, err := CheckOrigin("https://A.com", []string{"https://a.com"}); err == nil {
		t.Fatal("a duplicate was taken")
	}
}

func TestLoadTeamIsSealed(t *testing.T) {
	home := newHome(t)
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte(StarterTeam), 0o644)
	if _, err := LoadTeam(home); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte(StarterTeam+"# an agent wrote this\n"), 0o644)
	if _, err := LoadTeam(home); err == nil || !strings.Contains(err.Error(), "changed outside the founder's path") {
		t.Fatalf("%v", err)
	}
	if _, err := LoadTeam(filepath.Join(newHome(t), "nowhere")); err == nil || !os.IsNotExist(unwrap(err)) {
		t.Fatalf("%v", err)
	}
}

func unwrap(err error) error {
	for {
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return err
		}
		err = u.Unwrap()
	}
}

func newHome(t *testing.T) string {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} {
		t.Setenv(k, "")
	}
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatal(err)
	}
	return home
}

func TestLeadAndAuditorOfAnAgent(t *testing.T) {
	home := newHome(t)
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte("teams:\n  web: [web, web.dev, web.auditor]\n  ops: [ops.dev]\n"+
		"agents:\n  web: {}\n  web.dev: {}\n  web.auditor: {}\n  ops: {}\n  ops.dev: {}\n"), 0o644)
	tm, err := LoadTeam(home)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct{ agent, lead, auditor string }{
		{"web.dev", "web", "web.auditor"},
		{"web.auditor", "web", "web.auditor"},
		{"ops.dev", "ops", ""}, // no manager in its team: the agent its name hangs under
		{"web", "", "web.auditor"},
	} {
		if l, a := tm.LeadOf(c.agent), tm.AuditorOf(c.agent); l != c.lead || a != c.auditor {
			t.Errorf("%s: lead %q auditor %q, want %q %q", c.agent, l, a, c.lead, c.auditor)
		}
	}
	var none *Team
	if none.LeadOf("web.dev") != "" || none.AuditorOf("web.dev") != "" {
		t.Error("no team.yaml: no lead, no auditor")
	}
}
