package reads

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// flat is a proposal file: a map of scalars (the roster's dump writes nothing nested), keys in file order.
type flat struct {
	keys []string
	m    map[string]any
}

func (f flat) get(k string) any { return f.m[k] }

// text is a field as Python's str() shows it, "" for a missing one.
func (f flat) text(k string) string {
	switch v := f.m[k].(type) {
	case nil:
		return ""
	case string:
		return v
	case bool:
		if v {
			return "True"
		}
		return "False"
	default:
		return fmt.Sprint(v)
	}
}

// truthy is Python's truth of a field.
func (f flat) truthy(k string) bool {
	switch v := f.m[k].(type) {
	case nil:
		return false
	case string:
		return v != ""
	case bool:
		return v
	case int64:
		return v != 0
	}
	return true
}

var (
	hexInt = regexp.MustCompile(`^[-+]?0x[0-9a-fA-F]+$`)
	decInt = regexp.MustCompile(`^[-+]?(0|[1-9][0-9]*)$`)
	pyTrue = map[string]bool{"true": true, "True": true, "TRUE": true, "yes": true, "Yes": true, "YES": true, "on": true, "On": true, "ON": true}
	pyFals = map[string]bool{"false": true, "False": true, "FALSE": true, "no": true, "No": true, "NO": true, "off": true, "Off": true, "OFF": true}
)

// parseFlat reads one flat `key: scalar` file the way handline/infra/yamlcompat.py does for what dump writes:
// plain, single- or double-quoted scalars, integers, booleans and null. Anything else is an error, and the file is skipped.
func parseFlat(text string) (flat, error) {
	f := flat{m: map[string]any{}}
	for n, raw := range strings.Split(strings.NewReplacer("\r\n", "\n", "\r", "\n").Replace(text), "\n") {
		t := strings.TrimSpace(raw)
		if t == "" || strings.HasPrefix(t, "#") || t == "---" || t == "..." {
			continue
		}
		if raw[0] == ' ' || raw[0] == '\t' {
			return f, fmt.Errorf("line %d: nested", n+1)
		}
		k, v, ok := splitKey(t)
		if !ok {
			return f, fmt.Errorf("line %d: not a key", n+1)
		}
		val, err := scalar(v)
		if err != nil {
			return f, fmt.Errorf("line %d: %w", n+1, err)
		}
		if _, dup := f.m[k]; !dup {
			f.keys = append(f.keys, k)
		}
		f.m[k] = val
	}
	return f, nil
}

func splitKey(s string) (string, string, bool) {
	var quote byte
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case quote != 0:
			if c == '\\' && quote == '"' {
				i++
			} else if c == quote {
				quote = 0
			}
		case c == '"' || c == '\'':
			quote = c
		case c == ':' && (i+1 == len(s) || s[i+1] == ' ' || s[i+1] == '\t'):
			key, err := scalar(s[:i])
			ks, isStr := key.(string)
			if err != nil || !isStr {
				return "", "", false
			}
			return ks, s[i+1:], true
		}
	}
	return "", "", false
}

func scalar(tok string) (any, error) {
	t := strings.TrimSpace(tok)
	if t == "" || t == "null" || t == "Null" || t == "NULL" || t == "~" {
		return nil, nil
	}
	if t[0] == '"' || t[0] == '\'' {
		return unquote(t)
	}
	if i := strings.Index(t, " #"); i >= 0 {
		t = strings.TrimSpace(t[:i])
	}
	switch {
	case pyTrue[t]:
		return true, nil
	case pyFals[t]:
		return false, nil
	case hexInt.MatchString(t), decInt.MatchString(t):
		n, err := strconv.ParseInt(t, 0, 64)
		return n, err
	case strings.ContainsAny(t, "[]{}"):
		return nil, fmt.Errorf("flow value")
	}
	if _, err := strconv.ParseFloat(t, 64); err == nil {
		return nil, fmt.Errorf("number that is not an integer")
	}
	return t, nil
}

func unquote(t string) (any, error) {
	q := t[0]
	if len(t) < 2 {
		return nil, fmt.Errorf("unclosed quote")
	}
	var b strings.Builder
	for i := 1; i < len(t); i++ {
		c := t[i]
		switch {
		case q == '"' && c == '\\' && i+1 < len(t) && (t[i+1] == '"' || t[i+1] == '\\'):
			i++
			b.WriteByte(t[i])
		case c == q && q == '\'' && i+1 < len(t) && t[i+1] == '\'':
			i++
			b.WriteByte('\'')
		case c == q:
			if i != len(t)-1 {
				return nil, fmt.Errorf("text after the closing quote")
			}
			return b.String(), nil
		default:
			b.WriteByte(c)
		}
	}
	return nil, fmt.Errorf("unclosed quote")
}

var itemKey = regexp.MustCompile(`^i(\d+)\.(.+)$`)

// items are a team diff's steps: the `i<n>.<field>` keys grouped by n, in order of n.
func (f flat) items() []flat {
	groups := map[int]*flat{}
	var order []int
	for _, k := range f.keys {
		m := itemKey.FindStringSubmatch(k)
		if m == nil {
			continue
		}
		n, _ := strconv.Atoi(m[1])
		g, ok := groups[n]
		if !ok {
			g = &flat{m: map[string]any{}}
			groups[n] = g
			order = append(order, n)
		}
		g.keys = append(g.keys, m[2])
		g.m[m[2]] = f.m[k]
	}
	sort.Ints(order)
	out := make([]flat, len(order))
	for i, n := range order {
		out[i] = *groups[n]
	}
	return out
}

// sets are the `set.<key>` fields, key to text, in file order.
func (f flat) sets() ([]string, map[string]string) {
	var keys []string
	vals := map[string]string{}
	for _, k := range f.keys {
		if strings.HasPrefix(k, "set.") {
			keys = append(keys, k[4:])
			vals[k[4:]] = f.text(k)
		}
	}
	return keys, vals
}

var stepOrder = []string{"team", "add", "rename", "settings", "schedule", "link", "unlink", "retire", "context", "global"}

func stepIndex(kind string) int {
	for i, k := range stepOrder {
		if k == kind {
			return i
		}
	}
	return len(stepOrder)
}

func orStr(s, alt string) string {
	if s == "" {
		return alt
	}
	return s
}

func members(p flat) []string {
	var out []string
	for _, m := range strings.Split(p.text("members"), ",") {
		if m != "" {
			out = append(out, m)
		}
	}
	return out
}

func isEdge(kind string) bool { return kind == "link" || kind == "unlink" }

// title is the card's head: `link a -> b`, `retire a`, `bundle 3 changes`.
func (f flat) title() string {
	if f.text("kind") == "bundle" {
		return fmt.Sprintf("bundle %d changes", len(f.items()))
	}
	t := f.text("kind") + " " + f.text("agent")
	if isEdge(f.text("kind")) {
		t += " -> " + f.text("to")
	}
	return t
}

// changeLines is what approving one change does, in words, a line for each thing it changes.
func changeLines(it flat) []string {
	kind, agent, to := it.text("kind"), it.text("agent"), it.text("to")
	switch kind {
	case "link":
		line := fmt.Sprintf("allow %s to ask %s (response %s", agent, to, orStr(it.text("response"), "async"))
		if it.truthy("relation") {
			line += ", relation " + it.text("relation")
		}
		return []string{line + ")"}
	case "unlink":
		return []string{fmt.Sprintf("remove the edge %s -> %s", agent, to)}
	case "add":
		line := "register " + agent
		for _, k := range []string{"team", "repo", "model", "effort", "vendor"} {
			if it.truthy(k) {
				line += ", " + k + " " + it.text(k)
			}
		}
		return []string{line}
	case "team":
		line := "create team " + agent
		if it.truthy("repo") {
			line += " (working in " + it.text("repo") + ")"
		}
		if m := members(it); len(m) > 0 {
			line += " with members " + strings.Join(m, ", ")
		}
		return []string{line}
	case "settings":
		keys, vals := it.sets()
		if len(keys) == 0 {
			return []string{"change the settings of " + agent}
		}
		out := make([]string, len(keys))
		for i, k := range keys {
			out[i] = fmt.Sprintf("set %s of %s to %s", k, agent, orStr(vals[k], "(none)"))
		}
		return out
	case "context":
		keys, _ := it.sets()
		if len(keys) == 0 {
			return []string{"replace the standing context of " + agent}
		}
		out := make([]string, len(keys))
		for i, k := range keys {
			out[i] = fmt.Sprintf("replace %s of %s", k, agent)
		}
		return out
	case "rename":
		return []string{fmt.Sprintf("rename %s to %s", agent, to)}
	case "retire":
		return []string{"retire " + agent}
	}
	return []string{kind + " " + agent}
}

// diff is what approving does, in words, one change per line (the spec's Proposal.diff; not a unified diff): the changes of a bundle in the
// order they are applied.
func (f flat) diff() string {
	items := []flat{f}
	if f.text("kind") == "bundle" {
		items = f.items()
		sort.SliceStable(items, func(i, j int) bool { return stepIndex(items[i].text("kind")) < stepIndex(items[j].text("kind")) })
	}
	var lines []string
	for _, it := range items {
		lines = append(lines, changeLines(it)...)
	}
	return strings.Join(lines, "\n")
}

// subject is decisions.digest of the fields that change what applying the proposal does: what hr.decide checks back.
func (f flat) subject() string {
	fields := map[string]any{}
	for _, k := range []string{"kind", "agent", "to", "team", "repo", "model", "effort", "vendor",
		"response", "relation", "requested_by", "why", "members"} {
		fields[k] = f.get(k)
	}
	for _, k := range f.keys {
		if strings.HasPrefix(k, "repo.") || strings.HasPrefix(k, "set.") || itemKey.MatchString(k) {
			fields[k] = f.get(k)
		}
	}
	return sha256Hex(rpcPyJSON(fields))
}
