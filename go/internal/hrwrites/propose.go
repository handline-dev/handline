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
var proposable = []string{"add", "retire", "rename", "link", "unlink", "team", "context"}

// maxSteps bounds a team diff (MAX_STEPS in handline/team/hr.py).
const maxSteps = 40

func refuse(format string, a ...any) (any, error) {
	return nil, rpc.Refused(fmt.Sprintf(format, a...), "proposal", "")
}

func argStr(m map[string]any, k string) string { s, _ := m[k].(string); return s }

// argList reads `members`: a list, or the comma text a diff file may hold.
func argList(v any) []string {
	var out []string
	switch x := v.(type) {
	case string:
		for _, m := range strings.Split(x, ",") {
			if m != "" {
				out = append(out, m)
			}
		}
	case []any:
		for _, m := range x {
			if s, _ := m.(string); s != "" {
				out = append(out, s)
			}
		}
	}
	return out
}

// teamNames are a team's members' full names, or why not (_team_names in hr.py): nothing may exist yet, in the roster or in team.yaml.
func (e *Env) teamNames(name string, members []string, repos map[string]string, registered []string) ([]string, string) {
	if len(members) == 0 {
		return nil, "a team needs at least one member (--member); one agent is `hr add`"
	}
	var full []string
	for _, m := range members {
		if strings.HasPrefix(m, name+".") {
			full = append(full, m)
		} else {
			full = append(full, name+"."+m)
		}
	}
	for _, n := range append([]string{name}, full...) {
		if textsafe.HasUnsafe(n) || strings.ContainsAny(n, "/, ") {
			return nil, fmt.Sprintf("bad agent name: %q", n)
		}
	}
	seen := map[string]bool{}
	for _, n := range full {
		if seen[n] || n == name {
			return nil, "a member is named twice"
		}
		seen[n] = true
	}
	var taken []string
	for _, n := range append([]string{name}, full...) {
		if slices.Contains(registered, n) {
			taken = append(taken, n)
		}
	}
	if len(taken) > 0 {
		return nil, strings.Join(taken, ", ") + " already an agent"
	}
	var stray []string
	for m := range repos {
		if !slices.Contains(full, m) && m != name {
			stray = append(stray, m)
		}
	}
	if len(stray) > 0 {
		slices.Sort(stray)
		return nil, fmt.Sprintf("a repo was given for %s, who is not in the team", strings.Join(stray, ", "))
	}
	t, err := team.LoadTeam(e.Home)
	if err != nil {
		return nil, fmt.Sprintf("team.yaml cannot be read (%v)", err)
	}
	if _, ok := t.Teams[name]; ok {
		return nil, fmt.Sprintf("team %s is already in team.yaml", name)
	}
	var listed []string
	for _, n := range append([]string{name}, full...) {
		if _, ok := t.Agents[n]; ok {
			listed = append(listed, n)
		}
	}
	if len(listed) > 0 {
		return nil, strings.Join(listed, ", ") + " already in team.yaml agents"
	}
	return full, ""
}

// draft checks one change against the roster `registered` (and the agents an earlier step makes, `coming`) and returns it as a proposal, or why not (_draft in hr.py).
func (e *Env) draft(actor, kind string, args map[string]any, why string, registered []string, coming map[string]bool) (*Prop, string) {
	name := argStr(args, "name")
	str := func(k string) string { return argStr(args, k) }
	why = strings.TrimSpace(why)
	if !slices.Contains(proposable, kind) {
		return nil, fmt.Sprintf("kind %q is not raised this way yet: one of %s", kind, strings.Join(proposable, ", "))
	}
	if name == "" || strings.Contains(name, "/") || strings.HasPrefix(name, ".") || textsafe.HasUnsafe(name) {
		return nil, fmt.Sprintf("bad agent name: %q", name)
	}
	if why == "" {
		return nil, "say why: the founder is being asked to decide, not to rubber-stamp"
	}
	to, teamName, repo := str("to"), str("team"), str("repo")
	var full []string
	repos := map[string]string{}
	if raw, ok := args["repos"].(map[string]any); ok {
		for k, v := range raw {
			repos[k], _ = v.(string)
		}
	}
	switch kind {
	case "team":
		var msg string
		if full, msg = e.teamNames(name, argList(args["members"]), repos, registered); msg != "" {
			return nil, msg
		}
	case "add":
		if slices.Contains(registered, name) {
			return nil, fmt.Sprintf("%s is already an agent", name)
		}
	default:
		if !slices.Contains(registered, name) {
			return nil, fmt.Sprintf("%s is not an agent", name)
		}
	}
	if kind == "link" || kind == "unlink" {
		if msg := e.edgeRefusal(kind, name, to, "", "", registered, coming); msg != "" {
			return nil, msg
		}
	}
	if kind == "rename" {
		if to == "" || strings.Contains(to, "/") || strings.HasPrefix(to, ".") {
			return nil, "a rename needs a new name (--to)"
		}
		if textsafe.HasUnsafe(to) {
			return nil, fmt.Sprintf("bad agent name: %q", to)
		}
		if slices.Contains(registered, to) {
			return nil, fmt.Sprintf("%s is already an agent", to)
		}
	}
	if teamName != "" && textsafe.HasUnsafe(teamName) {
		return nil, fmt.Sprintf("bad team name: %q", teamName)
	}
	if eff := str("effort"); eff != "" && !slices.Contains(team.ValidEffort, eff) {
		return nil, fmt.Sprintf("effort must be one of %s", strings.Join(team.ValidEffort, ", "))
	}
	if v := str("vendor"); v != "" {
		if err := team.CheckVendor(v); err != nil {
			return nil, err.Error()
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
			return nil, "name ROLE.md or base.md, with the whole new text"
		}
		var texts [][2]string
		for fn, text := range sets {
			texts = append(texts, [2]string{fn, text})
		}
		slices.SortFunc(texts, func(a, b [2]string) int { return strings.Compare(a[0], b[0]) })
		if why := contextProblem(texts); why != "" {
			return nil, why
		}
	}
	if kind == "add" && repo == "" && teamName != "" {
		if cfg, err := team.NewRoster(e.Home).Read(teamName); err == nil {
			repo, _ = cfg["repo"].(string)
		}
	}
	if kind == "add" && repo == "" {
		return nil, fmt.Sprintf("%s needs a repo: --repo <path> (or --team of a manager that has one). Its worktree is made there; without one it never starts.", name)
	}
	if kind == "team" && repo == "" {
		return nil, fmt.Sprintf("team %s needs --repo <path> for its manager; a member without its own --member SUFFIX=REPO works there too. Each worktree is made in it.", name)
	}
	idb := make([]byte, 4)
	if _, err := rand.Read(idb); err != nil {
		return nil, err.Error()
	}
	prop := &Prop{}
	for _, kv := range []yamlcompat.Pair{
		{Key: "id", Val: hex.EncodeToString(idb)}, {Key: "kind", Val: kind}, {Key: "agent", Val: name},
		{Key: "requested_by", Val: actor}, {Key: "why", Val: why}, {Key: "created", Val: time.Now().Unix()}, {Key: "state", Val: "proposed"},
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
		if kind == "context" { // a proposal file holds no line break; PyJSON matches the Python that still reads it, drop both together
			v = rpc.PyJSON(v, false, true)
		}
		prop.set("set."+fn, v)
	}
	if len(full) > 0 {
		prop.set("members", strings.Join(full, ","))
		for _, n := range full {
			r := repos[n]
			if r == "" {
				r = repo // written into every agent.yaml
			}
			prop.set("repo."+n, r)
		}
	}
	return prop, ""
}

// raised writes the proposal down outside the transaction; the Effect only records the reply.
func (e *Env) raised(c *rpc.Conn, prop *Prop, said string) (any, error) {
	if err := e.save(prop); err != nil {
		return refuse("could not write the proposal down, so nothing was raised: %v", err)
	}
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		return map[string]any{"accepted": true, "proposal": proposalNumber(prop.id()), "said": said}, nil
	})
}

// proposeMethod is hr.propose (hr.propose in handline/team/hr.py): check what was asked for and write it down for the founder. Nothing changes yet.
func (e *Env) proposeMethod(c *rpc.Conn, params map[string]any) (any, error) {
	args, _ := params["args"].(map[string]any)
	if argStr(params, "kind") == bundle {
		changes, _ := args["changes"].([]any)
		return e.proposeBundle(c, changes, argStr(params, "why"))
	}
	prop, why := e.draft(c.Caller.Actor(), argStr(params, "kind"), args, argStr(params, "why"), paths.ListAgents(e.Home), nil)
	if why != "" {
		return refuse("%s", why)
	}
	return e.raised(c, prop, fmt.Sprintf("proposal %s raised. Nothing changes until the founder decides it; an approve is applied then, and you are told how it went.", prop.id()))
}

// proposeBundle is hr.propose of kind bundle (propose_bundle in hr.py): a whole team diff, each step judged against the roster the steps before it leave, in the order they are applied.
func (e *Env) proposeBundle(c *rpc.Conn, raw []any, why string) (any, error) {
	why = strings.TrimSpace(why)
	if why == "" {
		return refuse("say why: the founder is being asked to decide, not to rubber-stamp")
	}
	if len(raw) == 0 {
		return refuse("a team diff needs at least one change")
	}
	if len(raw) > maxSteps {
		return refuse("a team diff is at most %d changes; split it", maxSteps)
	}
	known := []string{"kind", "name", "team", "repo", "model", "to", "effort", "vendor", "response", "relation", "members", "repos", "sets"}
	type step struct {
		n    int
		kind string
		args map[string]any
	}
	var steps []step
	for i, r := range raw {
		ch, _ := r.(map[string]any)
		kind := argStr(ch, "kind")
		if kind == "" || argStr(ch, "name") == "" {
			return refuse("change %d needs a kind and a name", i+1)
		}
		if !slices.Contains(itemKinds, kind) {
			return refuse("change %d: kind must be one of %s", i+1, strings.Join(itemKinds, ", "))
		}
		var extra []string
		for k := range ch {
			if !slices.Contains(known, k) {
				extra = append(extra, k)
			}
		}
		if len(extra) > 0 {
			slices.Sort(extra)
			return refuse("change %d: not an argument: %s", i+1, strings.Join(extra, ", "))
		}
		steps = append(steps, step{i + 1, kind, ch})
	}
	slices.SortStableFunc(steps, func(a, b step) int { return stepIndex(a.kind) - stepIndex(b.kind) })
	now := paths.ListAgents(e.Home)
	registered := now
	coming := map[string]bool{}
	out := &Prop{}
	var flat []yamlcompat.Pair
	for n, s := range steps {
		d, msg := e.draft(c.Caller.Actor(), s.kind, s.args, why, registered, coming)
		if msg != "" {
			return refuse("change %d (%s %s): %s", s.n, s.kind, argStr(s.args, "name"), msg)
		}
		for _, kv := range d.pairs {
			switch kv.Key {
			case "id", "requested_by", "why", "created", "state":
			default:
				flat = append(flat, yamlcompat.Pair{Key: fmt.Sprintf("i%d.%s", n+1, kv.Key), Val: kv.Val})
			}
		}
		registered = rosterAfter(registered, d)
		for _, a := range registered {
			if !slices.Contains(now, a) {
				coming[a] = true
			}
		}
	}
	idb := make([]byte, 4)
	if _, err := rand.Read(idb); err != nil {
		return nil, err
	}
	for _, kv := range []yamlcompat.Pair{
		{Key: "id", Val: hex.EncodeToString(idb)}, {Key: "kind", Val: bundle}, {Key: "agent", Val: fmt.Sprintf("%d changes", len(steps))},
		{Key: "requested_by", Val: c.Caller.Actor()}, {Key: "why", Val: why}, {Key: "created", Val: time.Now().Unix()}, {Key: "state", Val: "proposed"},
	} {
		out.set(kv.Key, kv.Val)
	}
	for _, kv := range flat {
		out.set(kv.Key, kv.Val)
	}
	return e.raised(c, out, fmt.Sprintf("proposal %s raised: %d changes, decided once and applied together or not at all. Nothing changes until the founder decides it; you are told how it went.", out.id(), len(steps)))
}

// rosterAfter is the roster once the step d has been applied.
func rosterAfter(registered []string, d *Prop) []string {
	names := map[string]bool{}
	for _, n := range registered {
		names[n] = true
	}
	switch d.kind() {
	case "add":
		names[d.str("agent")] = true
	case "team":
		names[d.str("agent")] = true
		for _, m := range d.members() {
			names[m] = true
		}
	case "retire":
		delete(names, d.str("agent"))
	case "rename":
		delete(names, d.str("agent"))
		names[d.str("to")] = true
	}
	out := make([]string, 0, len(names))
	for n := range names {
		out = append(out, n)
	}
	slices.Sort(out)
	return out
}

func proposalNumber(pid string) int64 {
	n, _ := hexNum(pid)
	return n
}
