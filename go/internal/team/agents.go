// Package team is handline/team: the agents' and accounts' files, the routing policy, standing consent for folders and an
// agent's settings. It reads and writes the same files the Python modules do, and refuses what they refuse.
package team

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/yamlcompat"
)

const (
	DefaultModel = "sonnet"
	// DefaultEffort is a cost lever as real as the model: each agent declares its own.
	DefaultEffort = "medium"
	// ContextTokens is the default context-handoff threshold (session/triggers.py).
	ContextTokens = 150_000

	MaxExtraWorktrees  = 2
	BrowserServer      = "browser"
	DefaultBrowserProf = "Profile 17"
	DefaultMode        = "auto"

	BaseBudgetBytes = 2048 // base.md <= 2KB
	RoleBudgetBytes = 1024 // ROLE.md <= 1KB

	skipNS = "skip_permissions"
)

var (
	ValidEffort = []string{"low", "medium", "high", "xhigh", "max"}
	// EffortShort are the effort names short enough for a column; an unknown value is shown whole.
	EffortShort = map[string]string{"low": "low", "medium": "mid", "high": "high", "xhigh": "xhi", "max": "max"}
	// Modes are Claude's permission modes. bypass is the old skip_permissions switch.
	Modes = []string{"default", "auto", "acceptEdits", "plan", "bypass"}

	// NotText is said wherever a name is refused for what YAML would make of it.
	NotText = "It would not be read back as text from the YAML files it is written into (no, on, null, numbers and dates are not); choose another."
)

// Config is an agent.yaml read: its top-level map.
type Config = map[string]any

func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case int64:
		return x != 0
	case float64:
		return x != 0
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

// pyStr is str() of a decoded value.
func pyStr(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case bool:
		if x {
			return "True"
		}
		return "False"
	case string:
		return x
	case float64:
		return yamlcompat.FloatRepr(x)
	}
	return fmt.Sprint(v)
}

// orDefault is str(cfg.get(k) or def).
func orDefault(cfg Config, k, def string) string {
	if v := cfg[k]; truthy(v) {
		return pyStr(v)
	}
	return def
}

// Parts is (vendor, model, effort) as configured, defaults filled in.
func Parts(cfg Config) (vendor, model, effort string) {
	return orDefault(cfg, "vendor", DefaultVendor), orDefault(cfg, "model", DefaultModel), orDefault(cfg, "effort", DefaultEffort)
}

func positiveInt(v any) (int64, bool) {
	n, ok := v.(int64)
	return n, ok && n > 0
}

// ContextTokensOf is the agent's own context-handoff threshold: configured, or the default for an absent, falsy or garbage value.
func ContextTokensOf(cfg Config) int64 {
	if n, ok := positiveInt(cfg["context_tokens"]); ok {
		return n
	}
	return ContextTokens
}

// ExtraWorktrees is how many worktrees an agent has besides its first, 0 to MaxExtraWorktrees. Only a real count turns one on.
func ExtraWorktrees(cfg Config) int64 {
	if n, ok := positiveInt(cfg["extra_worktrees"]); ok {
		return min(n, MaxExtraWorktrees)
	}
	return 0
}

// SkipPermissions is whether this agent's sessions start without asking before they act. Only a real true turns it on.
func SkipPermissions(cfg Config) bool { return cfg["skip_permissions"] == true }

// Chrome is whether this agent's sessions start with the browser integration. Only a real true turns it on.
func Chrome(cfg Config) bool { return cfg["chrome"] == true }

// MCPConfigText is text as one line of JSON, if it is a {"mcpServers": {...}} object (what --mcp-config takes).
func MCPConfigText(text string) (string, error) {
	v, err := decodeOrdered(text)
	if err != nil {
		return "", err
	}
	o, ok := v.(*jobj)
	if !ok {
		return "", errors.New(`not a {"mcpServers": {...}} object`)
	}
	if _, ok := o.vals["mcpServers"].(*jobj); !ok {
		return "", errors.New(`not a {"mcpServers": {...}} object`)
	}
	return encodeCompact(o), nil
}

// MCPConfigArgs is this agent's own MCP servers as --mcp-config <json>. They come from agent.yaml, which is sealed, not from a
// project .mcp.json. A value that is not a valid config adds nothing.
func MCPConfigArgs(cfg Config) []string {
	v, ok := cfg["mcp_config"].(string)
	if !ok {
		return nil
	}
	text, err := MCPConfigText(v)
	if err != nil {
		return nil
	}
	return []string{"--mcp-config", text}
}

var (
	originRe   = regexp.MustCompile(`^https?://[A-Za-z0-9.-]+(:[0-9]+)?$`)
	originsArg = regexp.MustCompile(`--allowed-origins "([^"]*)"`)
	profileArg = regexp.MustCompile(`--profile-dir-name "([^"]*)"`)
)

// browserCommand is the shell line of the agent's own browser MCP server, or "".
func browserCommand(cfg Config) string {
	text := "{}"
	if truthy(cfg["mcp_config"]) {
		text = pyStr(cfg["mcp_config"])
	}
	v, err := decodeOrdered(text)
	if err != nil {
		return ""
	}
	o, ok := v.(*jobj)
	if !ok {
		return ""
	}
	servers, ok := o.vals["mcpServers"].(*jobj)
	if !ok {
		return ""
	}
	srv, ok := servers.vals[BrowserServer].(*jobj)
	if !ok {
		return ""
	}
	args, ok := srv.vals["args"].([]any)
	if !ok || len(args) == 0 {
		return ""
	}
	return jsonText(args[len(args)-1])
}

// jsonText is str() of a decoded JSON value, for the cases a command line can be.
func jsonText(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case nil:
		return "None"
	case bool:
		return pyStr(x)
	}
	return fmt.Sprint(v)
}

// BrowserMode is how this agent browses: "chrome", "playwright" (its own sandboxed browser server with a site list), "custom" (a
// browser server set by hand that has none, left alone) or "off".
func BrowserMode(cfg Config) string {
	if Chrome(cfg) {
		return "chrome"
	}
	cmd := browserCommand(cfg)
	if cmd == "" {
		return "off"
	}
	if originsArg.MatchString(cmd) {
		return "playwright"
	}
	return "custom"
}

// BrowserOrigins are the sites its Playwright server may open.
func BrowserOrigins(cfg Config) []string {
	m := originsArg.FindStringSubmatch(browserCommand(cfg))
	out := []string{}
	if m == nil {
		return out
	}
	for _, o := range strings.Split(m[1], ";") {
		if o != "" {
			out = append(out, o)
		}
	}
	return out
}

// ParseOrigins are the origins in text (separated by spaces, commas, semicolons or lines); an error for one that is not
// http(s)://host[:port]. They end up inside a shell line, so nothing else is let through.
func ParseOrigins(text string) ([]string, error) {
	out := []string{}
	for _, o := range strings.Fields(strings.NewReplacer(",", " ", ";", " ").Replace(text)) {
		o = strings.TrimRight(o, "/")
		if !originRe.MatchString(o) {
			return nil, fmt.Errorf("%s is not a site like https://example.com", pyQuote(o))
		}
		seen := false
		for _, x := range out {
			seen = seen || x == o
		}
		if !seen {
			out = append(out, o)
		}
	}
	return out, nil
}

func pyQuote(s string) string {
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		return `"` + s + `"`
	}
	return "'" + strings.ReplaceAll(s, "'", `\'`) + "'"
}

// CheckOrigin is text as one origin, if it is one and others does not have it.
func CheckOrigin(text string, others []string) (string, error) {
	sites, err := ParseOrigins(text)
	if err != nil {
		return "", err
	}
	if len(sites) != 1 {
		return "", errors.New("write one site, like https://example.com")
	}
	for _, o := range others {
		if strings.EqualFold(o, sites[0]) {
			return "", fmt.Errorf("%s is already in the list", sites[0])
		}
	}
	return sites[0], nil
}

// BrowserMCPConfig is the mcp_config text for mode: the agent's other MCP servers as they are, and its browser server set to
// origins for "playwright" or removed otherwise; "" when nothing is left.
func BrowserMCPConfig(home string, cfg Config, mode string, origins []string) (string, error) {
	servers := newJobj()
	text := "{}"
	if truthy(cfg["mcp_config"]) {
		text = pyStr(cfg["mcp_config"])
	}
	if v, err := decodeOrdered(text); err == nil {
		if o, ok := v.(*jobj); ok {
			if s, ok := o.vals["mcpServers"].(*jobj); ok {
				for _, k := range s.keys {
					servers.set(k, s.vals[k])
				}
			}
		}
	}
	profile := DefaultBrowserProf
	if m := profileArg.FindStringSubmatch(browserCommand(cfg)); m != nil {
		profile = m[1]
	}
	if mode == "custom" {
		return "", errors.New("a custom browser config is set in a terminal, not here")
	}
	servers.del(BrowserServer) // any mode picked replaces a server set by hand
	if mode == "playwright" {
		if len(origins) == 0 {
			return "", errors.New("name at least one site for the sandboxed browser")
		}
		srv := newJobj()
		srv.set("command", "sh")
		srv.set("args", []any{"-c", fmt.Sprintf(`PLAYWRIGHT_MCP_EXTENSION_TOKEN=$(cat %s) exec %s --extension --profile-dir-name "%s" --allowed-origins "%s"`,
			shlexQuote(filepath.Join(home, "browser", "token")),
			shlexQuote(filepath.Join(home, "browser", "node_modules", ".bin", "playwright-mcp")), profile, strings.Join(origins, ";"))})
		servers.set(BrowserServer, srv)
	}
	if len(servers.keys) == 0 {
		return "", nil
	}
	top := newJobj()
	top.set("mcpServers", servers)
	return encodeCompact(top), nil
}

// Kind is what an agent is, for a column: the model and its effort, the vendor drawn only when it is not the default's.
func Kind(cfg Config) string {
	vendor, model, effort := Parts(cfg)
	short, ok := EffortShort[effort]
	if !ok {
		short = effort
	}
	out := model + " " + short
	if vendor == DefaultVendor {
		return out
	}
	return vendor + " " + out
}

// Roster is the agents of one home: their files, the seals on them and the founder's grants.
type Roster struct {
	Home  string
	Store *protected.Store
	Seals *sealed.Seals
}

// NewRoster is the roster of home.
func NewRoster(home string) *Roster {
	s := sealed.New(home)
	return &Roster{Home: home, Store: s.Store, Seals: s}
}

// SkipPermissionsGranted is whether the founder turned it on, through Register from a terminal or the screen. agent.yaml alone
// never does: the grant is kept in the protected store, where an agent cannot write.
func (r *Roster) SkipPermissionsGranted(agent string) bool {
	v, ok := r.Store.Get(skipNS, agent)
	return ok && v == "granted"
}

// SkipPermissionsInEffect is what a launch will do: configured, and granted.
func (r *Roster) SkipPermissionsInEffect(agent string, cfg Config) bool {
	return SkipPermissions(cfg) && r.SkipPermissionsGranted(agent)
}

// ModeOf is the permission mode a launch will use: bypass when skipping is configured and granted, else the agent.yaml mode
// when it names one, else auto. Anything else reads as the default, never as more than was asked.
func (r *Roster) ModeOf(agent string, cfg Config) string {
	if r.SkipPermissionsInEffect(agent, cfg) {
		return "bypass"
	}
	if v, ok := cfg["mode"].(string); ok && v != "bypass" && contains(Modes, v) {
		return v
	}
	return DefaultMode
}

func contains(xs []string, x string) bool {
	for _, e := range xs {
		if e == x {
			return true
		}
	}
	return false
}

// SandboxOn is whether the sandbox is on: only with `sandbox: true` in agent.yaml. Off is the default, so the file
// needs no grant: an agent that writes it only confines itself.
func (r *Roster) SandboxOn(agent string, cfg Config) bool {
	return cfg["sandbox"] == true
}

// AgentFile is the path of an agent's agent.yaml.
func (r *Roster) AgentFile(name string) string {
	return filepath.Join(paths.AgentDir(r.Home, name), "agent.yaml")
}

func pairsGet(p []yamlcompat.Pair, k string) (any, bool) {
	for _, x := range p {
		if x.Key == k {
			return x.Val, true
		}
	}
	return nil, false
}

func pairsSet(p []yamlcompat.Pair, k string, v any) []yamlcompat.Pair {
	for i := range p {
		if p[i].Key == k {
			p[i].Val = v
			return p
		}
	}
	return append(p, yamlcompat.Pair{Key: k, Val: v})
}

func pairsPop(p []yamlcompat.Pair, k string) []yamlcompat.Pair {
	for i := range p {
		if p[i].Key == k {
			return append(p[:i:i], p[i+1:]...)
		}
	}
	return p
}

// Write replaces agent.yaml with pairs and seals it. The founder's session only.
func (r *Roster) Write(name string, pairs []yamlcompat.Pair) error {
	if err := r.Seals.RequireFounder(); err != nil {
		return err
	}
	text, err := yamlcompat.Dump(pairs)
	if err != nil {
		return err
	}
	if err := os.WriteFile(r.AgentFile(name), []byte(text), 0o644); err != nil {
		return err
	}
	return r.Seals.Seal(sealed.AgentKey(name), text)
}

// SetKeys sets keys of the agent's agent.yaml (a nil value removes the key) in one write, and seals the file.
func (r *Roster) SetKeys(name string, set []yamlcompat.Pair) error {
	_, pairs, err := r.readOrdered(name)
	if err != nil {
		return err
	}
	for _, kv := range set {
		if kv.Val == nil {
			pairs = pairsPop(pairs, kv.Key)
		} else {
			pairs = pairsSet(pairs, kv.Key, kv.Val)
		}
	}
	return r.Write(name, pairs)
}

// Read is the agent's agent.yaml, refused when it is not what was sealed, is not a map, or holds a control character.
func (r *Roster) Read(name string) (Config, error) {
	cfg, _, err := r.readOrdered(name)
	return cfg, err
}

// NotRegistered is the error for an agent with no agent.yaml.
type NotRegistered struct{ Name, Path string }

func (e *NotRegistered) Error() string {
	return fmt.Sprintf("agent '%s' is not registered (%s)", e.Name, e.Path)
}

func (r *Roster) readOrdered(name string) (Config, []yamlcompat.Pair, error) {
	p := r.AgentFile(name)
	b, err := os.ReadFile(p)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil, &NotRegistered{name, p}
	}
	if err != nil {
		return nil, nil, err
	}
	if !utf8.Valid(b) {
		return nil, nil, &yamlcompat.ConfigError{Msg: p + ": not valid UTF-8"}
	}
	text := string(b)
	if err := r.Seals.Check(sealed.AgentKey(name), text, p); err != nil {
		return nil, nil, err
	}
	pairs, isMap, err := yamlcompat.ParsePairs(text)
	if err != nil {
		return nil, nil, err
	}
	if !isMap {
		return nil, nil, &yamlcompat.ConfigError{Msg: p + ": top level is not a map"}
	}
	cfg := Config{}
	for _, x := range pairs {
		cfg[x.Key] = x.Val
	}
	// REFUSE, not strip: a value Dump would not have written must not silently run as something other than what the file says.
	if err := yamlcompat.RefuseUnsafeValues(cfg, p); err != nil {
		return nil, nil, err
	}
	return cfg, pairs, nil
}

// RegisterOpts is what Register is asked to set; a nil field is left as it is.
type RegisterOpts struct {
	Role, Weight, RoleFile, Repo           string
	Model, Effort, Vendor, Mode, MCPConfig *string
	ContextTokens, ExtraWorktrees          *int64
	SkipPermissions, Chrome, Sandbox       *bool
}

func strp(s string) *string { return &s }

func present(p *string) bool { return p != nil && *p != "" }

// Register creates or updates an agent: its folders, its agent.yaml (sealed) and the founder's grants. A name is stored, drawn
// on every screen and typed into terminals, so a control byte in it is refused here.
func (r *Roster) Register(name string, o RegisterOpts) (string, error) {
	if name == "" || strings.Contains(name, "/") || strings.HasPrefix(name, ".") || textsafe.HasUnsafe(name) {
		return "", fmt.Errorf("bad agent name: %s", pyQuote(name))
	}
	if !yamlcompat.IsPlainText(name) {
		return "", fmt.Errorf("bad agent name: %s. %s", pyQuote(name), NotText)
	}
	if present(o.Effort) && !contains(ValidEffort, *o.Effort) {
		return "", fmt.Errorf("effort must be one of %s, not %s", strings.Join(ValidEffort, ", "), pyQuote(*o.Effort))
	}
	if present(o.Vendor) {
		if err := CheckVendor(*o.Vendor); err != nil {
			return "", err
		}
	}
	// First, so an agent's refusal leaves nothing half-written: the grant is the founder's alone.
	if err := r.Seals.RequireFounder(); err != nil {
		return "", err
	}
	skip := o.SkipPermissions
	if o.Mode != nil {
		if !contains(Modes, *o.Mode) {
			return "", fmt.Errorf("mode must be one of %s, not %s", strings.Join(Modes, ", "), pyQuote(*o.Mode))
		}
		b := *o.Mode == "bypass" // bypass is the old switch; any other mode turns it off
		skip = &b
	}
	if skip != nil && *skip {
		if err := r.Store.Put(skipNS, name, "granted"); err != nil {
			return "", err
		}
	} else if skip != nil {
		if err := r.Store.Delete(skipNS, name); err != nil {
			return "", err
		}
	}
	d := paths.AgentDir(r.Home, name)
	f := r.AgentFile(name)
	var text string
	_, statErr := os.Stat(f)
	isNew := statErr != nil
	if isNew {
		// Built as a map and written by Dump, which reads every line back; built before the folder exists, so a value it refuses leaves nothing.
		weight := o.Weight
		if weight == "" {
			weight = "light"
		}
		pairs := []yamlcompat.Pair{{Key: "name", Val: name}, {Key: "model", Val: orStr(o.Model, DefaultModel)},
			{Key: "effort", Val: orStr(o.Effort, DefaultEffort)}, {Key: "weight", Val: weight}, {Key: "enabled", Val: true}}
		if o.Repo != "" {
			pairs = append(pairs, yamlcompat.Pair{Key: "repo", Val: o.Repo})
		}
		if present(o.Vendor) && *o.Vendor != DefaultVendor {
			pairs = append(pairs, yamlcompat.Pair{Key: "vendor", Val: *o.Vendor})
		}
		if skip != nil && *skip {
			pairs = append(pairs, yamlcompat.Pair{Key: "skip_permissions", Val: true})
		}
		if o.Mode != nil && *o.Mode != "bypass" && *o.Mode != DefaultMode {
			pairs = append(pairs, yamlcompat.Pair{Key: "mode", Val: *o.Mode})
		}
		if o.Sandbox != nil && *o.Sandbox {
			pairs = append(pairs, yamlcompat.Pair{Key: "sandbox", Val: true})
		}
		if o.Chrome != nil && *o.Chrome {
			pairs = append(pairs, yamlcompat.Pair{Key: "chrome", Val: true})
		}
		if o.ExtraWorktrees != nil && *o.ExtraWorktrees > 0 {
			pairs = append(pairs, yamlcompat.Pair{Key: "extra_worktrees", Val: min(*o.ExtraWorktrees, MaxExtraWorktrees)})
		}
		if present(o.MCPConfig) {
			t, err := MCPConfigText(*o.MCPConfig)
			if err != nil {
				return "", err
			}
			pairs = append(pairs, yamlcompat.Pair{Key: "mcp_config", Val: t})
		}
		var err error
		if text, err = yamlcompat.Dump(pairs); err != nil {
			return "", err
		}
	}
	for _, sub := range []string{"inbox", "journal", "memory"} {
		if err := os.MkdirAll(filepath.Join(d, sub), 0o755); err != nil {
			return "", err
		}
	}
	if isNew {
		if err := os.WriteFile(f, []byte(text), 0o644); err != nil {
			return "", err
		}
		if err := r.Seals.Seal(sealed.AgentKey(name), text); err != nil {
			return "", err
		}
	} else if err := r.update(name, f, o, skip); err != nil {
		return "", err
	}
	rolePath := filepath.Join(d, "ROLE.md")
	if o.RoleFile != "" {
		b, err := os.ReadFile(expandUser(o.RoleFile))
		if err != nil {
			return "", fmt.Errorf("--role-file not found: %s", expandUser(o.RoleFile))
		}
		if err := os.WriteFile(rolePath, b, 0o644); err != nil {
			return "", err
		}
	} else if _, err := os.Stat(rolePath); err != nil {
		role := o.Role
		if role == "" {
			role = "(describe the role, under 1KB)"
		}
		if err := os.WriteFile(rolePath, []byte("# "+name+"\n\n"+role+"\n"), 0o644); err != nil {
			return "", err
		}
	}
	base := filepath.Join(d, "base.md")
	if _, err := os.Stat(base); err != nil {
		body := "# base - " + name + "\n\n(injected into every session, so it is fixed overhead. Keep it under 2KB.)\n"
		if err := os.WriteFile(base, []byte(body), 0o644); err != nil {
			return "", err
		}
	}
	return d, nil
}

func orStr(p *string, def string) string {
	if present(p) {
		return *p
	}
	return def
}

func expandUser(p string) string {
	if p == "~" || strings.HasPrefix(p, "~/") {
		return filepath.Join(paths.UserHome(), strings.TrimPrefix(p, "~"))
	}
	return p
}

// update changes what was asked for in an agent.yaml that exists and leaves the rest of the map alone: re-serialized from the
// parsed document rather than spliced.
func (r *Roster) update(name, f string, o RegisterOpts, skip *bool) error {
	b, err := os.ReadFile(f)
	if err != nil {
		return err
	}
	cfg, isMap, err := yamlcompat.ParsePairs(string(b))
	if err != nil {
		return err
	}
	if !isMap {
		return &yamlcompat.ConfigError{Msg: f + ": top level is not a map"}
	}
	if present(o.Model) {
		cfg = pairsSet(cfg, "model", *o.Model)
	}
	if present(o.Effort) {
		cfg = pairsSet(cfg, "effort", *o.Effort)
	}
	if o.Weight != "" {
		cfg = pairsSet(cfg, "weight", o.Weight)
	}
	if o.Repo != "" {
		cfg = pairsSet(cfg, "repo", o.Repo)
	}
	if o.ContextTokens != nil && *o.ContextTokens != 0 {
		cfg = pairsSet(cfg, "context_tokens", *o.ContextTokens)
	}
	if o.ExtraWorktrees != nil {
		n := *o.ExtraWorktrees
		if n < 0 || n > MaxExtraWorktrees {
			return fmt.Errorf("extra_worktrees is 0 to %d, not %d", MaxExtraWorktrees, n)
		}
		if n != 0 {
			cfg = pairsSet(cfg, "extra_worktrees", n)
		} else {
			cfg = pairsPop(cfg, "extra_worktrees")
		}
	}
	if skip != nil {
		if *skip {
			cfg = pairsSet(cfg, "skip_permissions", true)
		} else {
			cfg = pairsPop(cfg, "skip_permissions")
		}
	}
	if o.Mode != nil {
		if *o.Mode == "bypass" || *o.Mode == DefaultMode {
			cfg = pairsPop(cfg, "mode")
		} else {
			cfg = pairsSet(cfg, "mode", *o.Mode)
		}
	}
	if o.Sandbox != nil {
		if *o.Sandbox {
			cfg = pairsSet(cfg, "sandbox", true)
		} else {
			cfg = pairsPop(cfg, "sandbox")
		}
	}
	if o.Chrome != nil {
		if *o.Chrome {
			cfg = pairsSet(cfg, "chrome", true)
		} else {
			cfg = pairsPop(cfg, "chrome")
		}
	}
	if o.MCPConfig != nil {
		if *o.MCPConfig != "" {
			t, err := MCPConfigText(*o.MCPConfig)
			if err != nil {
				return err
			}
			cfg = pairsSet(cfg, "mcp_config", t)
		} else {
			cfg = pairsPop(cfg, "mcp_config")
		}
	}
	if present(o.Vendor) && *o.Vendor != DefaultVendor {
		cfg = pairsSet(cfg, "vendor", *o.Vendor)
	}
	if _, has := pairsGet(cfg, "effort"); !has {
		cfg = pairsSet(cfg, "effort", DefaultEffort)
	}
	return r.Write(name, cfg)
}

// RepoOf is where this agent works, when it has been told.
func (r *Roster) RepoOf(name string) string {
	cfg, err := r.Read(name)
	if err != nil {
		return ""
	}
	if v := cfg["repo"]; truthy(v) {
		return pyStr(v)
	}
	return ""
}

// Querier is what a read needs: a database or a transaction.
type Querier interface {
	QueryRow(query string, args ...any) *sql.Row
}

// CurrentAccount is the account this agent's session is on; "" when none is recorded. A session id belongs to one account's
// config directory, so a resume has no other answer to give than this.
func CurrentAccount(db Querier, name string) (string, error) {
	var acct sql.NullString
	err := db.QueryRow("SELECT account FROM agent_state WHERE agent = ?", name).Scan(&acct)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return acct.String, err
}

// AccountReading is which account an agent's usage is landing on and how sure of it we are: recorded (a launch, or the process
// check wrote it), reported (the agent's own fresh snapshot named it) or assumed (a guess before a launch confirmed it).
type AccountReading struct{ Account, Source string }

// ReadAccount is the reading, for reading and never for choosing where a launch goes. reported is the agent's own status
// snapshot's account when it is fresh and open ("" when none): the usage module's, passed in. ok is false when nothing says.
func ReadAccount(db *sql.DB, name string, reported func() string) (AccountReading, bool, error) {
	var acct, source sql.NullString
	err := db.QueryRow("SELECT account, account_source FROM agent_state WHERE agent = ?", name).Scan(&acct, &source)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return AccountReading{}, false, err
	}
	has := err == nil && acct.String != ""
	if has && source.String != "assumed" {
		return AccountReading{acct.String, "recorded"}, true, nil
	}
	if reported != nil {
		if a := reported(); a != "" {
			return AccountReading{a, "reported"}, true, nil
		}
	}
	if has {
		return AccountReading{acct.String, "assumed"}, true, nil
	}
	return AccountReading{}, false, nil
}

// BudgetFile is one file of the per-session fixed overhead: its size, its budget and whether it is over.
type BudgetFile struct {
	File         string
	Bytes, Limit int64
	Over         bool
}

// BudgetReport is base.md and ROLE.md against their budgets.
func BudgetReport(home, name string) []BudgetFile {
	var out []BudgetFile
	for _, f := range []struct {
		name  string
		limit int64
	}{{"base.md", BaseBudgetBytes}, {"ROLE.md", RoleBudgetBytes}} {
		var size int64
		if st, err := os.Stat(filepath.Join(paths.AgentDir(home, name), f.name)); err == nil {
			size = st.Size()
		}
		out = append(out, BudgetFile{f.name, size, f.limit, size > f.limit})
	}
	return out
}
