package reads

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
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

const (
	defaultVendor = "claude"
	defaultLinks  = "  - an agent may ask its parent (its name up to the last dot), its children and its siblings\n" +
		"  - a manager may ask the founder (a decision task)"
)

func orStr(s, alt string) string {
	if s == "" {
		return alt
	}
	return s
}

// asked is `, label value` for each of the labelled fields the proposal sets.
func (f flat) asked(pairs ...string) string {
	var b strings.Builder
	for i := 0; i < len(pairs); i += 2 {
		if f.truthy(pairs[i]) {
			b.WriteString(", " + pairs[i+1] + " " + f.text(pairs[i]))
		}
	}
	return b.String()
}

func (f flat) vendorWords() string {
	if f.truthy("vendor") && f.text("vendor") != defaultVendor {
		return ", vendor " + f.text("vendor")
	}
	return ""
}

// body is what applying one change does, in words, as handline/team/hr.py _body says it; the unlink caveat, which reads
// the live team.yaml, is left out.
func (e *Env) body(p flat) []string {
	agent, to := p.text("agent"), p.text("to")
	switch p.text("kind") {
	case "link":
		rel := ""
		if p.truthy("relation") {
			rel = ", relation " + p.text("relation")
		}
		return []string{fmt.Sprintf("%s would be allowed to ask %s (response %s%s). One edge is added to team.yaml; nothing else in the routing changes.",
			agent, to, orStr(p.text("response"), "async"), rel)}
	case "unlink":
		return []string{fmt.Sprintf("The edge %s -> %s would be removed from team.yaml. Requests already queued on it stay queued.", agent, to)}
	case "add":
		in := ""
		if p.truthy("team") {
			in = " in team " + p.text("team")
		}
		repo := ""
		if p.truthy("repo") {
			repo = ", working in " + p.text("repo")
		}
		return []string{"It would be registered" + in + repo + p.asked("model", "model", "effort", "effort") + p.vendorWords() + ".",
			"team.yaml gains its name; nothing else in the routing changes."}
	case "team":
		mgr := ""
		if p.truthy("repo") {
			mgr = " (working in " + p.text("repo") + ")"
		}
		lines := []string{fmt.Sprintf("Team %s would be created: manager %s%s, and", agent, agent, mgr)}
		for _, n := range members(p) {
			w := ""
			if p.truthy("repo." + n) {
				w = " (working in " + p.text("repo."+n) + ")"
			}
			lines = append(lines, "  "+n+w)
		}
		return append(lines, "all registered"+p.asked("model", "model", "effort", "effort")+p.vendorWords()+
			". team.yaml gains the team and the agents, no edge; the default links apply:", defaultLinks)
	case "settings":
		keys, vals := p.sets()
		lines := []string{agent + "'s settings would become:"}
		for _, k := range keys {
			lines = append(lines, "  "+k+": "+orStr(vals[k], "(none)"))
		}
		return append(lines, "Written as the founder's own change, from its next launch; a running session is not restarted.")
	case "context":
		lines := []string{agent + "'s standing context would be replaced, as a diff of what is there now:"}
		keys, vals := p.sets()
		sort.Strings(keys)
		for _, fn := range keys {
			var text string
			if json.Unmarshal([]byte(vals[fn]), &text) != nil {
				text = ""
			}
			was, _ := os.ReadFile(filepath.Join(e.Home, "agents", agent, fn))
			d := unifiedDiff(splitLines(string(was)), splitLines(text), fn+" (now)", fn+" (proposed)", 2)
			if len(d) == 0 {
				d = []string{fn + ": no change"}
			}
			lines = append(lines, d...)
		}
		return append(lines, "Written from the next launch; a running session is not restarted.")
	case "schedule":
		_, st := p.sets()
		when := ""
		if st["cron"] != "" {
			when = scheduleWords(st["cron"])
		}
		shown := ""
		if st["sid"] != "" {
			shown = fmt.Sprintf("%s's schedule %s (%s: %s)", agent, st["index"], scheduleWords(st["cron_was"]), st["message_was"])
		}
		line := map[string]string{"add": fmt.Sprintf("%s would be typed, %s: %s", agent, when, st["message"]),
			"edit":   fmt.Sprintf("%s would become, %s: %s", shown, when, st["message"]),
			"remove": shown + " would be removed"}[st["action"]]
		return []string{line, "Written to its agent.yaml; the daemon reads it on its next tick."}
	case "global":
		_, st := p.sets()
		return []string{fmt.Sprintf("The global switch %s (%s) would be set to %s, for every agent without its own override.", agent, globalSwitchWords[agent], st["value"])}
	case "rename":
		in := ""
		if p.truthy("team") {
			in = " in team " + p.text("team")
		}
		return []string{fmt.Sprintf("%s would become %s%s: its folder, its journal and handoffs, its place in team.yaml and every request it ever sent or received move with the name.", agent, to, in),
			"It has to be closed first - an identity cannot change under a running session."}
	}
	return []string{agent + " would be taken out of team.yaml and its folder moved to agents/archive/. Its journal and handoffs are kept.",
		"Anything still queued for it stays queued and undeliverable."}
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

// diff is what approving does: each change's words, a context change as a unified diff of the file it replaces.
func (e *Env) diff(f flat) string {
	if f.text("kind") != "bundle" {
		return strings.Join(e.body(f), "\n")
	}
	items := f.items()
	sort.SliceStable(items, func(i, j int) bool { return stepIndex(items[i].text("kind")) < stepIndex(items[j].text("kind")) })
	var lines []string
	for n, it := range items {
		head := fmt.Sprintf("%d. %s %s", n+1, it.text("kind"), it.text("agent"))
		if isEdge(it.text("kind")) {
			head += " -> " + it.text("to")
		}
		lines = append(lines, head)
		for _, ln := range e.body(it) {
			lines = append(lines, "   "+ln)
		}
		lines = append(lines, "")
	}
	return strings.TrimRight(strings.Join(lines, "\n"), "\n")
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

func splitLines(s string) []string {
	s = strings.NewReplacer("\r\n", "\n", "\r", "\n").Replace(s)
	if s == "" {
		return nil
	}
	return strings.Split(strings.TrimSuffix(s, "\n"), "\n")
}

// unifiedDiff is difflib.unified_diff(a, b, from, to, lineterm="", n=n): nothing when the two are the same.
func unifiedDiff(a, b []string, from, to string, n int) []string {
	type op struct {
		tag            byte // '=', '-', '+'
		i1, i2, j1, j2 int
	}
	// longest common subsequence table, then the edit script walked from the front
	lcs := make([][]int, len(a)+1)
	for i := range lcs {
		lcs[i] = make([]int, len(b)+1)
	}
	for i := len(a) - 1; i >= 0; i-- {
		for j := len(b) - 1; j >= 0; j-- {
			if a[i] == b[j] {
				lcs[i][j] = lcs[i+1][j+1] + 1
			} else {
				lcs[i][j] = max(lcs[i+1][j], lcs[i][j+1])
			}
		}
	}
	var ops []op
	add := func(tag byte, i1, i2, j1, j2 int) {
		if l := len(ops); l > 0 && ops[l-1].tag == tag {
			ops[l-1].i2, ops[l-1].j2 = i2, j2
			return
		}
		ops = append(ops, op{tag, i1, i2, j1, j2})
	}
	i, j := 0, 0
	for i < len(a) || j < len(b) {
		switch {
		case i < len(a) && j < len(b) && a[i] == b[j]:
			add('=', i, i+1, j, j+1)
			i, j = i+1, j+1
		case i < len(a) && (j == len(b) || lcs[i+1][j] >= lcs[i][j+1]):
			add('-', i, i+1, j, j)
			i++
		default:
			add('+', i, i, j, j+1)
			j++
		}
	}
	same := true
	for _, o := range ops {
		same = same && o.tag == '='
	}
	if same {
		return nil
	}
	rng := func(start, stop int) string {
		length := stop - start
		if length == 1 {
			return strconv.Itoa(start + 1)
		}
		if length == 0 {
			start--
		}
		return fmt.Sprintf("%d,%d", start+1, length)
	}
	// difflib's grouped opcodes: n lines of context each side of a change, a run of more than 2n equal lines splits hunks
	if ops[0].tag == '=' {
		o := ops[0]
		ops[0] = op{'=', max(o.i1, o.i2-n), o.i2, max(o.j1, o.j2-n), o.j2}
	}
	if l := len(ops); ops[l-1].tag == '=' {
		o := ops[l-1]
		ops[l-1] = op{'=', o.i1, min(o.i2, o.i1+n), o.j1, min(o.j2, o.j1+n)}
	}
	var groups [][]op
	var group []op
	for _, o := range ops {
		if o.tag == '=' && o.i2-o.i1 > 2*n {
			group = append(group, op{'=', o.i1, min(o.i2, o.i1+n), o.j1, min(o.j2, o.j1+n)})
			groups = append(groups, group)
			group = []op{{'=', max(o.i1, o.i2-n), o.i2, max(o.j1, o.j2-n), o.j2}}
			continue
		}
		group = append(group, o)
	}
	if len(group) > 0 && !(len(group) == 1 && group[0].tag == '=') {
		groups = append(groups, group)
	}
	out := []string{"--- " + from, "+++ " + to}
	for _, g := range groups {
		out = append(out, fmt.Sprintf("@@ -%s +%s @@", rng(g[0].i1, g[len(g)-1].i2), rng(g[0].j1, g[len(g)-1].j2)))
		for _, o := range g {
			switch o.tag {
			case '=':
				for x := o.i1; x < o.i2; x++ {
					out = append(out, " "+a[x])
				}
			case '-':
				for x := o.i1; x < o.i2; x++ {
					out = append(out, "-"+a[x])
				}
			case '+':
				for y := o.j1; y < o.j2; y++ {
					out = append(out, "+"+b[y])
				}
			}
		}
	}
	return out
}

var globalSwitchWords = map[string]string{
	"idle-timeout":        "how long an idle agent may sit before it is closed (seconds, or never)",
	"notice-digest":       "how long after its first waiting result an agent is told them in one digest",
	"ignore-cache-expiry": "whether a resume may reuse a session whose prompt cache has expired",
	"auto-accept":         "whether ocp answers the dialogs a working agent's standing rules cover",
	"dev-mode":            "whether the daemon reloads its own code when it changes",
}

var weekdays = []string{"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"}

// scheduleWords is a cron expression in words when it is one the schedule form makes ("weekdays 09:00"), else the expression itself.
func scheduleWords(expr string) string {
	f := strings.Fields(expr)
	if len(f) != 5 || f[2] != "*" || f[3] != "*" {
		return expr
	}
	m, h, dow := f[0], f[1], f[4]
	if strings.HasPrefix(m, "*/") && h == "*" && dow == "*" && isDigits(m[2:]) {
		n, _ := strconv.Atoi(m[2:])
		return fmt.Sprintf("every %d minutes", n)
	}
	if !isDigits(m) {
		return expr
	}
	mi, _ := strconv.Atoi(m)
	if h == "*" && dow == "*" {
		return fmt.Sprintf("every hour at :%02d", mi)
	}
	if !isDigits(h) {
		return expr
	}
	hi, _ := strconv.Atoi(h)
	at := fmt.Sprintf("%02d:%02d", hi, mi)
	switch {
	case dow == "*":
		return "every day " + at
	case dow == "1-5":
		return "weekdays " + at
	case isDigits(dow):
		if d, _ := strconv.Atoi(dow); d >= 0 && d <= 6 {
			return weekdays[d] + " " + at
		}
	}
	return expr
}
