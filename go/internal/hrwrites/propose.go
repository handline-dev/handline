package hrwrites

import (
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// proposable are the kinds `hl hr` raises from a session: the roster changes that need no settings, schedule or global checks.
var proposable = []string{"add", "retire", "link", "unlink", "context"}

// proposeMethod is hr.propose (hr.propose in handline/team/hr.py): check what was asked for and write it down for the founder. Nothing changes yet.
func (e *Env) proposeMethod(c *rpc.Conn, params map[string]any) (any, error) {
	args, _ := params["args"].(map[string]any)
	pick := func(m map[string]any, k string) string { s, _ := m[k].(string); return s }
	str := func(k string) string { return pick(args, k) }
	kind, name, why := pick(params, "kind"), str("name"), strings.TrimSpace(pick(params, "why"))
	refuse := func(format string, a ...any) (any, error) {
		return nil, rpc.Refused(fmt.Sprintf(format, a...), "proposal", "")
	}
	if !slices.Contains(proposable, kind) {
		return refuse("kind %q is not raised this way yet: one of %s", kind, strings.Join(proposable, ", "))
	}
	if name == "" || strings.Contains(name, "/") || strings.HasPrefix(name, ".") || textsafe.HasUnsafe(name) {
		return refuse("bad agent name: %q", name)
	}
	if why == "" {
		return refuse("say why: the founder is being asked to decide, not to rubber-stamp")
	}
	registered := paths.ListAgents(e.Home)
	to, teamName, repo := str("to"), str("team"), str("repo")
	switch kind {
	case "add":
		if slices.Contains(registered, name) {
			return refuse("%s is already an agent", name)
		}
	default:
		if !slices.Contains(registered, name) {
			return refuse("%s is not an agent", name)
		}
	}
	if kind == "link" || kind == "unlink" {
		if msg := e.edgeRefusal(kind, name, to, "", "", registered, nil); msg != "" {
			return refuse("%s", msg)
		}
	}
	if teamName != "" && textsafe.HasUnsafe(teamName) {
		return refuse("bad team name: %q", teamName)
	}
	if eff := str("effort"); eff != "" && !slices.Contains(team.ValidEffort, eff) {
		return refuse("effort must be one of %s", strings.Join(team.ValidEffort, ", "))
	}
	if v := str("vendor"); v != "" {
		if err := team.CheckVendor(v); err != nil {
			return refuse("%s", err.Error())
		}
	}
	sets := map[string]string{}
	if raw, ok := args["sets"].(map[string]any); ok {
		for k, v := range raw {
			s, _ := v.(string)
			sets[k] = s
		}
	}
	if kind == "context" {
		if len(sets) == 0 {
			return refuse("name ROLE.md or base.md, with the whole new text")
		}
		var texts [][2]string
		for fn, text := range sets {
			texts = append(texts, [2]string{fn, text})
		}
		slices.SortFunc(texts, func(a, b [2]string) int { return strings.Compare(a[0], b[0]) })
		if why := contextProblem(texts); why != "" {
			return refuse("%s", why)
		}
	}
	if kind == "add" && repo == "" && teamName != "" {
		if cfg, err := team.NewRoster(e.Home).Read(teamName); err == nil {
			repo, _ = cfg["repo"].(string)
		}
	}
	if kind == "add" && repo == "" {
		return refuse("%s needs a repo: --repo <path> (or --team of a manager that has one). Its worktree is made there; without one it never starts.", name)
	}
	idb := make([]byte, 4)
	if _, err := rand.Read(idb); err != nil {
		return nil, err
	}
	prop := &Prop{}
	for _, kv := range []yamlcompat.Pair{
		{Key: "id", Val: hex.EncodeToString(idb)}, {Key: "kind", Val: kind}, {Key: "agent", Val: name},
		{Key: "requested_by", Val: c.Caller.Actor()}, {Key: "why", Val: why}, {Key: "created", Val: time.Now().Unix()}, {Key: "state", Val: "proposed"},
	} {
		prop.set(kv.Key, kv.Val)
	}
	for _, kv := range [][2]string{{"team", teamName}, {"repo", repo}, {"model", str("model")}, {"effort", str("effort")}, {"to", to}, {"vendor", str("vendor")}} {
		if kv[1] != "" {
			prop.set(kv[0], kv[1])
		}
	}
	fns := make([]string, 0, len(sets))
	for fn := range sets {
		fns = append(fns, fn)
	}
	slices.Sort(fns)
	for _, fn := range fns {
		v := sets[fn]
		if kind == "context" { // a proposal file holds no line break
			v = rpc.PyJSON(v, false, true)
		}
		prop.set("set."+fn, v)
	}
	if err := e.save(prop); err != nil {
		return refuse("could not write the proposal down, so nothing was raised: %v", err)
	}
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		return map[string]any{"accepted": true, "proposal": proposalNumber(prop.id()), "said": fmt.Sprintf("proposal %s raised. Nothing changes until the founder decides it; "+
			"an approve is applied then, and you are told how it went.", prop.id())}, nil
	})
}

func proposalNumber(pid string) int64 {
	n, _ := hexNum(pid)
	return n
}
