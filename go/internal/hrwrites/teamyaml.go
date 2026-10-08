package hrwrites

import (
	"fmt"
	"os"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// Editing team.yaml (handline/team/hr.py): as text, because it is a hand-written policy file whose comments are the reasons each edge exists,
// and a round-trip through the loader would drop every one of them. The result is parsed and validated before it is written, and an
// unfamiliar shape is refused rather than guessed at.

const word = `[\p{L}\p{N}_]`

func configErr(format string, args ...any) error {
	return &yamlcompat.ConfigError{Msg: fmt.Sprintf(format, args...)}
}

func pyRepr(s string) string {
	if strings.ContainsAny(s, "'\\\n\r\t") {
		return fmt.Sprintf("%q", s)
	}
	return "'" + s + "'"
}

func splitLines(text string) []string {
	if text == "" {
		return nil
	}
	text = strings.NewReplacer("\r\n", "\n", "\r", "\n").Replace(text)
	text = strings.TrimSuffix(text, "\n")
	return strings.Split(text, "\n")
}

func joinLines(lines []string) string { return strings.Join(lines, "\n") + "\n" }

func insertAt(lines []string, at int, line string) []string {
	out := append([]string(nil), lines[:at]...)
	out = append(out, line)
	return append(out, lines[at:]...)
}

// block is (first, last) line indexes of a top-level block, exclusive of its header.
func block(lines []string, header string) (int, int, error) {
	for i, line := range lines {
		if strings.TrimRight(line, " \t\r\n\v\f") == header {
			j := i + 1
			for j < len(lines) && (strings.TrimSpace(lines[j]) == "" || strings.HasPrefix(lines[j], " ") || strings.HasPrefix(lines[j], "\t") ||
				strings.HasPrefix(strings.TrimLeft(lines[j], " \t"), "#")) {
				j++
			}
			return i, j, nil
		}
	}
	return 0, 0, configErr("team.yaml has no `%s` block", header)
}

// textOnly refuses to write into team.yaml a name YAML would not read back as text.
func textOnly(what, value string) error {
	if value != "" && !yamlcompat.IsPlainText(value) {
		return configErr("bad %s name: %s. %s", what, pyRepr(value), team.NotText)
	}
	return nil
}

func nameLine(name string) *regexp.Regexp {
	return regexp.MustCompile(`^\s*` + regexp.QuoteMeta(name) + `\s*:`)
}

var memberLine = regexp.MustCompile(`^(\s*` + word + `[\p{L}\p{N}_.-]*\s*:\s*\[)([^\]]*)(\].*)$`)

func members(list string) []string {
	var out []string
	for _, x := range strings.Split(list, ",") {
		if x = strings.TrimSpace(x); x != "" {
			out = append(out, x)
		}
	}
	return out
}

func addText(text, name, teamName string) (string, error) {
	if err := textOnly("agent", name); err != nil {
		return "", err
	}
	lines := splitLines(text)
	head, end, err := block(lines, "agents:")
	if err != nil {
		return "", err
	}
	re := nameLine(name)
	for _, ln := range lines[head+1 : end] {
		if re.MatchString(ln) {
			return "", configErr("%s is already in team.yaml", name)
		}
	}
	text = joinLines(insertAt(lines, end, "  "+name+": {}"))
	if teamName != "" {
		return joinTeam(text, name, teamName)
	}
	return text, nil
}

func joinTeam(text, name, teamName string) (string, error) {
	if err := textOnly("agent", name); err != nil {
		return "", err
	}
	if err := textOnly("team", teamName); err != nil {
		return "", err
	}
	lines := splitLines(text)
	head, end, err := block(lines, "teams:")
	if err != nil {
		return "", err
	}
	pattern := regexp.MustCompile(`^(\s*` + regexp.QuoteMeta(teamName) + `\s*:\s*\[)([^\]]*)(\].*)$`)
	for i := head + 1; i < end; i++ {
		if m := pattern.FindStringSubmatch(lines[i]); m != nil {
			list := members(m[2])
			if !contains(list, name) {
				list = append(list, name)
			}
			lines[i] = m[1] + strings.Join(list, ", ") + m[3]
			return joinLines(lines), nil
		}
	}
	return joinLines(insertAt(lines, end, "  "+teamName+": ["+name+"]")), nil
}

func removeText(text, name string) (string, error) {
	lines := splitLines(text)
	head, end, err := block(lines, "agents:")
	if err != nil {
		return "", err
	}
	re := nameLine(name)
	var out []string
	dropped := false
	for i, line := range lines {
		if head < i && i < end && re.MatchString(line) {
			dropped = true
			continue
		}
		if m := memberLine.FindStringSubmatch(line); m != nil && contains(members(m[2]), name) {
			var kept []string
			for _, x := range members(m[2]) {
				if x != name {
					kept = append(kept, x)
				}
			}
			line = m[1] + strings.Join(kept, ", ") + m[3]
		}
		out = append(out, line)
	}
	if !dropped {
		return "", configErr("%s is not in team.yaml's agents block", name)
	}
	return joinLines(out), nil
}

// renameText is the name changed everywhere it is written, and nothing else: the edges too.
func renameText(text, old, new, teamName string) (string, error) {
	if err := textOnly("agent", new); err != nil {
		return "", err
	}
	w := regexp.QuoteMeta(old)
	lines := splitLines(text)
	head, end, err := block(lines, "agents:")
	if err != nil {
		return "", err
	}
	re := nameLine(old)
	found := false
	for _, ln := range lines[head+1 : end] {
		found = found || re.MatchString(ln)
	}
	if !found {
		return "", configErr("%s is not in team.yaml's agents block", old)
	}
	own := regexp.MustCompile(`^(\s*)` + w + `(\s*:)`)
	edge := regexp.MustCompile(`((?:from|to)\s*:\s*)(["']?)` + w + `(["']?)(\s*[,}])`)
	for i, line := range lines {
		if strings.HasPrefix(strings.TrimLeft(line, " \t"), "#") {
			continue
		}
		if head < i && i < end {
			line = own.ReplaceAllString(line, "${1}"+strings.ReplaceAll(new, "$", "$$")+"${2}")
		}
		if m := memberLine.FindStringSubmatch(line); m != nil {
			list := members(m[2])
			for j, x := range list {
				if x == old {
					list[j] = new
				}
			}
			if teamName != "" {
				var kept []string
				for _, x := range list {
					if x != new {
						kept = append(kept, x)
					}
				}
				list = kept
			}
			line = m[1] + strings.Join(list, ", ") + m[3]
		}
		line = renameEdges(edge, line, new)
		lines[i] = line
	}
	text = joinLines(lines)
	if teamName != "" {
		return joinTeam(text, new, teamName)
	}
	return text, nil
}

// renameEdges renames the agent where a `from:` or `to:` names it (not inside a longer name, and with matching quotes).
func renameEdges(re *regexp.Regexp, line, new string) string {
	var b strings.Builder
	last := 0
	for _, m := range re.FindAllStringSubmatchIndex(line, -1) {
		if m[0] > 0 {
			prev, _ := lastRune(line[:m[0]])
			if strings.ContainsRune("-_", prev) || (prev >= '0' && prev <= '9') || (prev >= 'a' && prev <= 'z') || (prev >= 'A' && prev <= 'Z') || prev > 127 {
				continue
			}
		}
		if line[m[4]:m[5]] != line[m[6]:m[7]] {
			continue
		}
		b.WriteString(line[last:m[0]])
		b.WriteString(line[m[2]:m[3]] + line[m[4]:m[5]] + new + line[m[6]:m[7]] + line[m[8]:m[9]])
		last = m[1]
	}
	b.WriteString(line[last:])
	return b.String()
}

func lastRune(s string) (rune, int) {
	r := []rune(s)
	return r[len(r)-1], len(r)
}

type edgeLine struct {
	at       int
	from, to string
}

// edgeLines are the one-line edges in the edges block.
func edgeLines(lines []string, head, end int) []edgeLine {
	var out []edgeLine
	for i := head + 1; i < end; i++ {
		s := strings.TrimSpace(lines[i])
		if !strings.HasPrefix(s, "- {") {
			continue
		}
		v, err := yamlcompat.ParseOrdered("e: " + strings.TrimSpace(s[1:]))
		o, ok := v.(*yamlcompat.OMap)
		if err != nil || !ok {
			continue
		}
		e, ok := o.M["e"].(*yamlcompat.OMap)
		if !ok {
			continue
		}
		out = append(out, edgeLine{i, pyText(e.M["from"]), pyText(e.M["to"])})
	}
	return out
}

// pyText is str(v) where an absent key reads as "".
func pyText(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}

var relationRe = regexp.MustCompile(`^[\w.-]+$`)

// linkText adds one edge. It goes before the first edge with a wildcard or a team at an end, because the first match wins and a specific edge
// behind a wildcard one would never be reached.
func (e *Env) linkText(text, frm, to, response, relation string) (string, error) {
	if err := textOnly("agent", frm); err != nil {
		return "", err
	}
	if err := textOnly("agent", to); err != nil {
		return "", err
	}
	if relation != "" && !relationRe.MatchString(relation) {
		return "", configErr("bad relation: %s", pyRepr(relation))
	}
	lines := splitLines(text)
	head, end, err := block(lines, "edges:")
	if err != nil {
		return "", err
	}
	found := edgeLines(lines, head, end)
	for _, f := range found {
		if f.from == frm && f.to == to {
			return "", configErr("team.yaml already has the edge %s -> %s", frm, to)
		}
	}
	names := map[string]bool{}
	for _, n := range paths.ListAgents(e.Home) {
		names[n] = true
	}
	at := -1
	for _, f := range found {
		if !names[f.from] || !names[f.to] {
			at = f.at
			break
		}
	}
	if at < 0 {
		at = head + 1
		if len(found) > 0 {
			at = found[len(found)-1].at + 1
		}
	}
	parts := []string{fmt.Sprintf(`from: "%s"`, frm), fmt.Sprintf(`to: "%s"`, to)}
	if relation != "" {
		parts = append(parts, "relation: "+relation)
	}
	if response == "" {
		response = "async"
	}
	parts = append(parts, "response: "+response)
	return joinLines(insertAt(lines, at, "  - { "+strings.Join(parts, ", ")+" }")), nil
}

func unlinkText(text, frm, to string) (string, error) {
	lines := splitLines(text)
	head, end, err := block(lines, "edges:")
	if err != nil {
		return "", err
	}
	gone := map[int]bool{}
	for _, f := range edgeLines(lines, head, end) {
		if f.from == frm && f.to == to {
			gone[f.at] = true
		}
	}
	if len(gone) == 0 {
		return "", configErr("team.yaml has no one-line edge exactly %s -> %s to remove", frm, to)
	}
	var out []string
	for i, l := range lines {
		if !gone[i] {
			out = append(out, l)
		}
	}
	return joinLines(out), nil
}

func need(cond bool, why string) error {
	if !cond {
		return configErr("%s", why)
	}
	return nil
}

// editTeamYaml applies a text edit to team.yaml and writes it only if it is still valid.
func (e *Env) editTeamYaml(edit func(string) (string, error), present, absent string, verify func(*team.Team) error) (bool, string, error) {
	p := paths.TeamFile(e.Home)
	if _, err := os.Stat(p); err != nil {
		return false, fmt.Sprintf("%s does not exist - run `hl team init` first", p), nil
	}
	seals := sealed.New(e.Home)
	if err := seals.RequireFounder(); err != nil {
		return false, "", err
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return false, "", err
	}
	text := string(b)
	if err := seals.Check(sealed.Team, text, p); err != nil {
		return false, "", err
	}
	edited, err := edit(text)
	if err != nil {
		if _, isConfig := err.(*yamlcompat.ConfigError); isConfig {
			return false, err.Error(), nil
		}
		return false, "", err
	}
	if err := validateTeam(edited, present, absent, verify); err != nil {
		return false, fmt.Sprintf("that edit would break team.yaml (%s); nothing written", err), nil
	}
	backup := p[:len(p)-len(".yaml")] + ".yaml.bak"
	if err := os.WriteFile(backup, []byte(text), 0o644); err != nil {
		return false, "", err
	}
	if err := os.WriteFile(p, []byte(edited), 0o644); err != nil {
		return false, "", err
	}
	if err := seals.Seal(sealed.Team, edited); err != nil {
		return false, "", err
	}
	return true, "team.yaml updated (previous kept as team.yaml.bak)", nil
}

func validateTeam(edited, present, absent string, verify func(*team.Team) error) error {
	v, err := yamlcompat.ParseOrdered(edited)
	if err != nil {
		return err
	}
	doc, ok := v.(*yamlcompat.OMap)
	if !ok {
		return configErr("top level is not a map")
	}
	t, err := team.NewTeam(doc)
	if err != nil {
		return err
	}
	listed := map[string]bool{}
	switch a := doc.M["agents"].(type) {
	case *yamlcompat.OMap:
		for _, k := range a.Keys {
			listed[k] = true
		}
	}
	if present != "" && !listed[present] {
		return configErr("the edit did not take: %s missing from agents", present)
	}
	if absent != "" && listed[absent] {
		return configErr("the edit did not take: %s still in agents", absent)
	}
	if verify != nil {
		return verify(t)
	}
	return nil
}
