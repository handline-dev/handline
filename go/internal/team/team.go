package team

import (
	"errors"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// The routing policy of team.yaml (handline/team/team.py). An agent only ever knows a target and a message; whether the edge is
// allowed, the response and the kind are decided here, deterministically, with no model involved. Unlisted edges are denied.

const (
	// DefaultLinks are the links an agent has without any edge written for them: it may ask its parent (its name up to the last
	// dot), its children and its siblings, and a manager may ask the founder (a decision task).
	Human                = "human"
	DefaultMaxChainDepth = 4
	// AskWindowS is how long a refused message sent again unchanged is the same refusal, not a new denied row.
	AskWindowS  = 3600
	StarterTeam = `# team.yaml - routing policy. Unlisted edges are denied.
teams:
  eng: [backend]
  ops: [archivist]

agents:
  backend:   {}
  reviewer:  {}
  archivist: {}

edges:
  - { from: "*", to: "reviewer", relation: peer, response: async }
  - { from: "reviewer", to: "*", kind: verdict, response: async }
  - { from: "*", to: "archivist", relation: peer, response: fire_and_forget }
  # The founder. Not an agent - no account, no session, no terminal - but a
  # routable name, so an agent has somewhere to send a decision only a person
  # can make, and the person has a way back down.
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }

deny: []

limits:
  max_chain_depth: 4
`
)

// validResponse: blocking is deliberately absent; a requester holding an account while it waits is the most expensive idle.
var validResponse = []string{"async", "fire_and_forget"}

// ParentName is the agent a name hangs under: harbor.sea -> harbor; "" for a name with no dot.
func ParentName(agent string) string {
	i := strings.LastIndex(agent, ".")
	if i <= 0 {
		return ""
	}
	return agent[:i]
}

// Decision is what routing says about one request.
type Decision struct {
	Allowed    bool
	Reason     string
	Relation   *string
	Response   string
	Kind       *string
	ChainDepth int
}

// Edge is one edge or deny entry of team.yaml, its values as read.
type Edge struct {
	From, To                 any
	Relation, Response, Kind any
	Reason                   any
	has                      map[string]bool
}

// Team is team.yaml read.
type Team struct {
	Teams         map[string][]string
	teamOrder     []string
	Agents        map[string]map[string]any
	Edges, Deny   []Edge
	MaxChainDepth int
}

func omap(v any) (*yamlcompat.OMap, bool) { o, ok := v.(*yamlcompat.OMap); return o, ok }

func cerr(format string, a ...any) error {
	return &yamlcompat.ConfigError{Msg: fmt.Sprintf(format, a...)}
}

// pyInt is int(v) of a decoded value: an int, a float (truncated), a bool, or text that is one whole number.
func pyInt(v any) (int, error) {
	switch x := v.(type) {
	case int64:
		return int(x), nil
	case float64:
		return int(x), nil
	case bool:
		if x {
			return 1, nil
		}
		return 0, nil
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(x))
		if err != nil {
			return 0, fmt.Errorf("invalid literal for int(): %s", pyQuote(x))
		}
		return n, nil
	}
	return 0, fmt.Errorf("int() argument must be a string or a number, not %s", pyTypeName(v))
}

func pyTypeName(v any) string {
	switch v.(type) {
	case nil:
		return "NoneType"
	case []any:
		return "list"
	case *yamlcompat.OMap:
		return "dict"
	}
	return "object"
}

// pyRepr is repr() of a decoded value, for the cases a message shows.
func pyRepr(v any) string {
	switch x := v.(type) {
	case string:
		return pyQuote(x)
	case []any:
		parts := make([]string, len(x))
		for i, e := range x {
			parts[i] = pyRepr(e)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case *yamlcompat.OMap:
		parts := make([]string, len(x.Keys))
		for i, k := range x.Keys {
			parts[i] = pyQuote(k) + ": " + pyRepr(x.M[k])
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return pyStr(v)
}

// NewTeam builds the policy from a parsed document (ordered maps), refusing what could silently allow.
func NewTeam(doc *yamlcompat.OMap) (*Team, error) {
	t := &Team{Teams: map[string][]string{}, Agents: map[string]map[string]any{},
		MaxChainDepth: DefaultMaxChainDepth}
	if o, ok := omap(doc.M["teams"]); ok && truthy(o) {
		for _, name := range o.Keys {
			t.teamOrder = append(t.teamOrder, name)
			var members []string
			if list, ok := o.M[name].([]any); ok {
				for _, m := range list {
					members = append(members, pyStr(m))
				}
			}
			t.Teams[name] = members
		}
	}
	if o, ok := omap(doc.M["agents"]); ok {
		for _, name := range o.Keys {
			sub, ok := omap(o.M[name])
			if !ok {
				return nil, cerr("agents.%s is not a map", name)
			}
			m := map[string]any{}
			for k, v := range sub.M {
				m[k] = v
			}
			t.Agents[name] = m
		}
	}
	rawEdges, _ := doc.M["edges"].([]any)
	rawDeny, _ := doc.M["deny"].([]any)
	for i, item := range rawEdges {
		o, ok := omap(item)
		if !ok {
			return nil, cerr("edges[%d] is not a map", i)
		}
		if _, has := o.M["from"]; !has {
			return nil, cerr("edges[%d] needs both 'from' and 'to'", i)
		}
		if _, has := o.M["to"]; !has {
			return nil, cerr("edges[%d] needs both 'from' and 'to'", i)
		}
		resp, has := o.M["response"]
		if !has {
			resp = "async"
		}
		if s, ok := resp.(string); !ok || !contains(validResponse, s) {
			return nil, cerr("edges[%d].response=%s is not one of %s. 'blocking' is intentionally not supported.", i, pyRepr(resp), pyRepr(anyStrings(validResponse)))
		}
		if err := t.checkPattern(fmt.Sprintf("edges[%d].from", i), pyStr(o.M["from"])); err != nil {
			return nil, err
		}
		if err := t.checkPattern(fmt.Sprintf("edges[%d].to", i), pyStr(o.M["to"])); err != nil {
			return nil, err
		}
		t.Edges = append(t.Edges, edgeOf(o))
	}
	// deny needs the same scrutiny as edges, and more: an edge that fails to parse denies by default, but a deny that fails to
	// parse silently allows.
	for i, item := range rawDeny {
		o, ok := omap(item)
		if !ok {
			return nil, cerr("deny[%d] is not a map: %s. An unquoted value like 'bug#42' can do this.", i, pyRepr(item))
		}
		var missing []string
		for _, k := range []string{"from", "to"} {
			if _, has := o.M[k]; !has {
				missing = append(missing, k)
			}
		}
		if len(missing) > 0 {
			keys := make([]string, 0, len(o.M))
			for k := range o.M {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			present := strings.Join(keys, ", ")
			if present == "" {
				present = "none"
			}
			return nil, cerr("deny[%d] is missing %s (keys present: %s). A deny without both matches nothing and would silently allow.", i, strings.Join(missing, ", "), present)
		}
		if err := t.checkPattern(fmt.Sprintf("deny[%d].from", i), pyStr(o.M["from"])); err != nil {
			return nil, err
		}
		if err := t.checkPattern(fmt.Sprintf("deny[%d].to", i), pyStr(o.M["to"])); err != nil {
			return nil, err
		}
		t.Deny = append(t.Deny, edgeOf(o))
	}
	if limits, ok := omap(doc.M["limits"]); ok {
		var err error
		if v, has := limits.M["max_chain_depth"]; has {
			if t.MaxChainDepth, err = pyInt(v); err != nil {
				return nil, err
			}
		}
	}
	return t, nil
}

func anyStrings(xs []string) []any {
	out := make([]any, len(xs))
	for i, x := range xs {
		out[i] = x
	}
	return out
}

func edgeOf(o *yamlcompat.OMap) Edge {
	e := Edge{From: o.M["from"], To: o.M["to"], Relation: o.M["relation"], Response: o.M["response"], Kind: o.M["kind"],
		Reason: o.M["reason"], has: map[string]bool{}}
	for k := range o.M {
		e.has[k] = true
	}
	if !e.has["response"] {
		e.Response = "async"
	}
	return e
}

// checkPattern: a '<team>.*' pattern naming a team that does not exist matches nothing, so it would silently allow.
func (t *Team) checkPattern(where, pattern string) error {
	if strings.HasSuffix(pattern, ".*") && pattern != "*" {
		group := strings.TrimSuffix(pattern, ".*")
		if _, ok := t.Teams[group]; !ok {
			names := make([]string, 0, len(t.Teams))
			for n := range t.Teams {
				names = append(names, n)
			}
			sort.Strings(names)
			list := strings.Join(names, ", ")
			if list == "" {
				list = "none"
			}
			return cerr("%s=%s refers to team %s, which is not in teams (%s)", where, pyQuote(pattern), pyQuote(group), list)
		}
	}
	return nil
}

// LoadTeam reads team.yaml of home: sealed, one map.
func LoadTeam(home string) (*Team, error) {
	p := paths.TeamFile(home)
	b, err := os.ReadFile(p)
	if errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("%s not found - run `hl team init`: %w", p, os.ErrNotExist)
	}
	if err != nil {
		return nil, err
	}
	text := string(b)
	if err := sealed.New(home).Check(sealed.Team, text, p); err != nil {
		return nil, err
	}
	v, err := yamlcompat.ParseOrdered(text)
	if err != nil {
		return nil, err
	}
	doc, ok := omap(v)
	if !ok {
		return nil, cerr("%s: top level is not a map", p)
	}
	return NewTeam(doc)
}

// Members is the members of a group.
func (t *Team) Members(group string) []string { return append([]string(nil), t.Teams[group]...) }

// TeamOf is the first team (in file order) that has the agent as a member; "" when none does.
func (t *Team) TeamOf(agent string) string {
	for _, name := range t.teamOrder {
		if contains(t.Teams[name], agent) {
			return name
		}
	}
	return ""
}

// Matches: '*' matches anything, 'eng.*' matches members of team eng, otherwise the pattern is an agent name (glob allowed).
func (t *Team) Matches(pattern, agent string) bool {
	if pattern == "*" {
		return true
	}
	if strings.HasSuffix(pattern, ".*") {
		return contains(t.Members(strings.TrimSuffix(pattern, ".*")), agent)
	}
	if pattern == agent {
		return true
	}
	return fnmatchCase(agent, pattern)
}

var globCache = map[string]*regexp.Regexp{}

// fnmatchCase is fnmatch.fnmatchcase: * any run, ? one character, [seq] and [!seq] one of a set.
func fnmatchCase(name, pattern string) bool {
	re, ok := globCache[pattern]
	if !ok {
		re = regexp.MustCompile(globToRegexp(pattern))
		globCache[pattern] = re
	}
	return re.MatchString(name)
}

func globToRegexp(pat string) string {
	var b strings.Builder
	b.WriteString(`(?s)^`)
	rs := []rune(pat)
	for i := 0; i < len(rs); i++ {
		c := rs[i]
		switch c {
		case '*':
			b.WriteString(`.*`)
		case '?':
			b.WriteString(`.`)
		case '[':
			j := i + 1
			if j < len(rs) && rs[j] == '!' {
				j++
			}
			if j < len(rs) && rs[j] == ']' {
				j++
			}
			for j < len(rs) && rs[j] != ']' {
				j++
			}
			if j >= len(rs) {
				b.WriteString(`\[`)
				continue
			}
			set := string(rs[i+1 : j])
			set = strings.ReplaceAll(set, `\`, `\\`)
			neg := strings.HasPrefix(set, "!")
			if neg {
				set = "^" + set[1:]
			} else if strings.HasPrefix(set, "^") {
				set = `\` + set
			}
			b.WriteString("[" + set + "]")
			i = j
		default:
			b.WriteString(regexp.QuoteMeta(string(c)))
		}
	}
	b.WriteString(`$`)
	return b.String()
}

// Known is whether the target is routable: a declared agent, a team member, or a literal (non-wildcard) 'to' in an edge, which is
// how the founder's name human exists.
func (t *Team) Known(agent string) bool {
	if _, ok := t.Agents[agent]; ok || t.TeamOf(agent) != "" {
		return true
	}
	for _, e := range t.Edges {
		to := ""
		if e.has["to"] {
			to = pyStr(e.To)
		}
		if !strings.Contains(to, "*") && to == agent {
			return true
		}
	}
	return false
}

// Managers is team name -> its manager, the member named like the team.
func (t *Team) Managers() map[string]string {
	out := map[string]string{}
	for name, members := range t.Teams {
		if contains(members, name) {
			out[name] = name
		}
	}
	return out
}

func sp(s string) *string { return &s }

// DefaultLink is the link the default links give this pair, or nil.
func (t *Team) DefaultLink(requester, target string) *Decision {
	hit := func(why string, relation, kind *string) *Decision {
		return &Decision{Allowed: true, Reason: "default link: " + why, Relation: relation, Response: "async", Kind: kind}
	}
	mine, theirs := ParentName(requester), ParentName(target)
	switch {
	case theirs == requester:
		return hit(requester+" is "+target+"'s parent", sp("supervisor"), nil)
	case mine == target:
		return hit(requester+" is "+target+"'s child", sp("subordinate"), nil)
	case mine != "" && mine == theirs:
		return hit(requester+" and "+target+" are siblings under "+mine, sp("peer"), nil)
	}
	if _, isManager := t.Managers()[requester]; target == Human && isManager {
		return hit(requester+" may ask the founder", nil, sp("task"))
	}
	return nil
}

// edgeKind: escalate is the old spelling of a request to the founder, which is a task of theirs.
func edgeKind(k any) *string {
	switch x := k.(type) {
	case nil:
		return nil
	case string:
		if x == "escalate" {
			return sp("task")
		}
		return &x
	}
	s := pyStr(k)
	return &s
}

func optStr(v any) *string {
	if v == nil {
		return nil
	}
	s := pyStr(v)
	return &s
}

// Route decides a request. reply: this answers a request target sent to requester; it needs no edge back, but an explicit deny
// still wins.
func (t *Team) Route(requester, target string, chainDepth int, reply bool) Decision {
	if requester == target {
		return Decision{Reason: "an agent cannot ask itself", Response: "async"}
	}
	if !t.Known(target) && !(target == Human && t.DefaultLink(requester, target) != nil) {
		return Decision{Reason: fmt.Sprintf("unknown target '%s'", target), Response: "async"}
	}
	if chainDepth >= t.MaxChainDepth {
		return Decision{Reason: fmt.Sprintf("chain depth %d reached the limit %d (loop guard)", chainDepth, t.MaxChainDepth), Response: "async"}
	}
	// Explicit denies win over any allow, so a deny cannot be shadowed by an earlier wildcard edge.
	for _, d := range t.Deny {
		if t.Matches(pyStrOr(d.From, d.has["from"]), requester) && t.Matches(pyStrOr(d.To, d.has["to"]), target) {
			why := "denied by policy"
			if truthy(d.Reason) {
				why = pyStr(d.Reason)
			}
			return Decision{Reason: fmt.Sprintf("denied: %s -> %s: %s", requester, target, why), Response: "async"}
		}
	}
	// First match wins, like firewall rules: order in team.yaml is significant.
	for _, e := range t.Edges {
		if t.Matches(pyStr(e.From), requester) && t.Matches(pyStr(e.To), target) {
			return Decision{Allowed: true, Reason: fmt.Sprintf("edge %s -> %s", pyStr(e.From), pyStr(e.To)), Relation: optStr(e.Relation),
				Response: pyStr(e.Response), Kind: edgeKind(e.Kind), ChainDepth: chainDepth}
		}
	}
	if d := t.DefaultLink(requester, target); d != nil {
		d.ChainDepth = chainDepth
		return *d
	}
	if reply {
		return Decision{Allowed: true, Reason: fmt.Sprintf("reply: %s asked %s", target, requester), Response: "fire_and_forget", ChainDepth: chainDepth}
	}
	return Decision{Reason: fmt.Sprintf("denied: no edge declares %s -> %s (unlisted edges are denied by default)", requester, target), Response: "async"}
}

func pyStrOr(v any, has bool) string {
	if !has {
		return ""
	}
	return pyStr(v)
}
