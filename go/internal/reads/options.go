package reads

import (
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

// knownModels is agent_settings.known_models: the families usage/prices.py prices by name, which the claude CLI takes as they are.
var knownModels = []string{"fable", "mythos", "opus", "sonnet", "haiku"}

var validEfforts = []string{"low", "medium", "high", "xhigh", "max"}

// optionsRole is rpc_options._role_line: the first line of ROLE.md that is not its own title, as one line of at most 200 characters.
func (e *Env) optionsRole(name string) string {
	b, err := os.ReadFile(filepath.Join(paths.AgentDir(e.Home, name), "ROLE.md"))
	if err != nil {
		return ""
	}
	var lines []string
	for _, ln := range strings.Split(string(b), "\n") {
		if strings.TrimSpace(ln) != "" {
			lines = append(lines, strings.TrimSpace(strings.TrimLeft(strings.TrimSpace(ln), "#")))
		}
	}
	pick := ""
	if len(lines) > 1 && strings.HasPrefix(strings.ToLower(lines[0]), strings.ToLower(name)) {
		pick = lines[1]
	} else if len(lines) > 0 {
		pick = lines[0]
	}
	if r := []rune(pick); len(r) > 200 {
		pick = string(r[:200])
	}
	return pick
}

func (e *Env) freeNames() []any {
	t, err := team.LoadTeam(e.Home)
	if err != nil {
		return []any{}
	}
	have := map[string]bool{}
	for _, a := range paths.ListAgents(e.Home) {
		have[a] = true
	}
	var names []string
	for a := range t.Agents {
		if !have[a] {
			names = append(names, a)
		}
	}
	sort.Strings(names)
	out := make([]any, len(names))
	for i, n := range names {
		out[i] = n
	}
	return out
}

// agentOptions is agent.options: what the new-agent form offers.
func (e *Env) agentOptions(_ *rpc.Conn, _ map[string]any) (any, error) {
	roles := []any{}
	for _, n := range paths.ListAgents(e.Home) {
		roles = append(roles, map[string]any{"name": n, "description": e.optionsRole(n)})
	}
	accts := []any{}
	for _, a := range paths.ListAccounts(e.Home) {
		in, known := e.loginState(a)
		accts = append(accts, map[string]any{"name": a, "login_state": loginName(in, known)})
	}
	return map[string]any{"repos": e.repos(), "roles": roles, "models": strs(knownModels), "efforts": strs(validEfforts),
		"accounts": accts, "free_names": e.freeNames()}, nil
}

func strs(xs []string) []any {
	out := make([]any, len(xs))
	for i, x := range xs {
		out[i] = x
	}
	return out
}

func loginName(in, known bool) string {
	switch {
	case !known:
		return "unknown"
	case in:
		return "ok"
	}
	return "needs_login"
}
