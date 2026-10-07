// Package yamlcompat reads the subset of YAML handline's config files use (handline/infra/yamlcompat.py, its bundled
// parser): nested maps, lists (including lists of maps), comments, quoted strings, flow collections and the YAML 1.1
// booleans PyYAML takes. Anything it is not sure it reads as PyYAML does is a ConfigError, never a guess: anchors, block
// scalars, merge keys, tabs, several documents, unbalanced flow collections, sexagesimals, dates, inf and nan, numbers
// written with underscores, double-quote escapes other than \\ and \", and a plain key PyYAML would not read as text.
//
// Values are nil, bool, int64, float64, string, []any and map[string]any (the order of a map's keys is not kept; Dump takes
// pairs in the order to write).
package yamlcompat

import (
	"fmt"
	"math"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/textsafe"
)

// ConfigError is what a file that cannot be read safely says.
type ConfigError struct{ Msg string }

func (e *ConfigError) Error() string { return e.Msg }

func fail(format string, a ...any) error { return &ConfigError{fmt.Sprintf(format, a...)} }

var unsupported = []string{"&", "*", "|", ">", "!!", "<<"}

var (
	pyTrue  = set("true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON")
	pyFalse = set("false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF")
	pyNull  = set("null", "Null", "NULL", "~")
	// What Dump quotes: anything a YAML 1.1 reader might take for one of them, in any case, including y and n.
	yTrue  = set("true", "yes", "on", "y")
	yFalse = set("false", "no", "off", "n")
)

func set(xs ...string) map[string]bool {
	m := map[string]bool{}
	for _, x := range xs {
		m[x] = true
	}
	return m
}

var (
	sexagesimal = regexp.MustCompile(`^[-+]?[0-9][0-9_]*(:[0-5]?[0-9])+(\.[0-9_]*)?$`)
	hexRe       = regexp.MustCompile(`^[-+]?0x[0-9a-fA-F]+$`)
	binRe       = regexp.MustCompile(`^[-+]?0b[01]+$`)
	octRe       = regexp.MustCompile(`^[-+]?0[0-7]+$`)
	decRe       = regexp.MustCompile(`^[-+]?(0|[1-9][0-9]*)$`)
	floatRe     = regexp.MustCompile(`^([-+]?[0-9]+\.[0-9]*|\.[0-9]+)([eE][-+][0-9]+)?$`)
	pyNumber    = regexp.MustCompile(`^(?:[-+]?0b[01_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+` +
		`|[-+]?[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?)$`)
	infNan    = regexp.MustCompile(`^(?:[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$`)
	timestamp = regexp.MustCompile(`^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}` +
		`|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}` +
		`(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)$`)
)

func strip(s string) string { return strings.TrimFunc(s, unicode.IsSpace) }

// closesAtEnd is whether the quote t opens with is first closed by its last character.
func closesAtEnd(t string) bool {
	quote := t[0]
	for i := 1; i < len(t); {
		ch := t[i]
		if quote == '"' && ch == '\\' {
			i += 2
			continue
		}
		if ch == quote {
			if quote == '\'' && i+1 < len(t) && t[i+1] == '\'' {
				i += 2
				continue
			}
			return i == len(t)-1
		}
		i++
	}
	return false
}

// unquote is the text of a quoted scalar. Single quotes double a quote to write one; double quotes know only the two
// escapes Dump writes, since any other is one this parser would read differently from PyYAML.
func unquote(t string, line int) (string, error) {
	body := t[1 : len(t)-1]
	if t[0] == '\'' {
		return strings.ReplaceAll(body, "''", "'"), nil
	}
	var out strings.Builder
	for i := 0; i < len(body); {
		r, w := utf8.DecodeRuneInString(body[i:])
		if r == '\\' {
			if i+1 >= len(body) || (body[i+1] != '"' && body[i+1] != '\\') {
				esc := body[i:]
				if len(esc) > 2 {
					_, w2 := utf8.DecodeRuneInString(body[i+1:])
					esc = body[i : i+1+w2]
				}
				return "", fail("line %d: escape %s in %s is not supported; use single quotes", line, pyRepr(esc), pyRepr(t))
			}
			i++
			r, w = utf8.DecodeRuneInString(body[i:])
		}
		out.WriteRune(r)
		i += w
	}
	return out.String(), nil
}

// pyRepr is a string as Python's repr writes it for the plain cases the messages show.
func pyRepr(s string) string {
	q := "'"
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		q = `"`
	}
	s = strings.ReplaceAll(s, `\`, `\\`)
	if q == "'" {
		s = strings.ReplaceAll(s, "'", `\'`)
	}
	return q + s + q
}

func key(raw string, line int) (string, error) {
	k := strip(raw)
	if k != "" && (k[0] == '"' || k[0] == '\'') {
		if !closesAtEnd(k) {
			return "", fail("line %d: quoted key is not closed at its end: %s", line, pyRepr(k))
		}
		return unquote(k, line)
	}
	for _, u := range unsupported {
		if strings.HasPrefix(k, u) {
			return "", fail("line %d: key %s starts with %s, which is YAML syntax (an anchor, alias, tag, block scalar or merge key); quote it if you mean the text",
				line, pyRepr(k), pyRepr(u))
		}
	}
	got, err := scalar(k, line)
	if err != nil {
		return "", err
	}
	if s, ok := got.(string); !ok || s != k {
		return "", fail("line %d: key %s is read as %s by PyYAML; quote it if you mean the text", line, pyRepr(k), pyValue(got))
	}
	return k, nil
}

func pyValue(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case bool:
		if x {
			return "True"
		}
		return "False"
	case string:
		return pyRepr(x)
	}
	return fmt.Sprint(v)
}

func parseInt(sign, digits string, base int, tok string, line int) (any, error) {
	n, err := strconv.ParseInt(digits, base, 64)
	if err != nil {
		return nil, fail("line %d: %s is too large for this reader; quote it if you mean the string", line, pyRepr(tok))
	}
	if sign == "-" {
		n = -n
	}
	return n, nil
}

func splitSign(t string) (string, string) {
	if t != "" && (t[0] == '-' || t[0] == '+') {
		return t[:1], t[1:]
	}
	return "", t
}

func scalar(tok string, line int) (any, error) {
	t := strip(tok)
	if t == "" {
		return nil, nil
	}
	if (strings.HasPrefix(t, "[") && strings.HasSuffix(t, "]")) || (strings.HasPrefix(t, "{") && strings.HasSuffix(t, "}")) {
		return flow(t, line)
	}
	if strings.HasPrefix(t, "[") || strings.HasPrefix(t, "{") || strings.HasSuffix(t, "]") || strings.HasSuffix(t, "}") {
		return nil, fail("line %d: unbalanced flow collection: %s", line, pyRepr(t))
	}
	if t[0] == '"' || t[0] == '\'' {
		if !closesAtEnd(t) {
			return nil, fail("line %d: quoted scalar is not closed at its end: %s", line, pyRepr(t))
		}
		return unquote(t, line)
	}
	low := strings.ToLower(t)
	switch {
	case pyNull[t]:
		return nil, nil
	case pyTrue[t]:
		return true, nil
	case pyFalse[t]:
		return false, nil
	}
	if low == "inf" || low == "-inf" || low == "+inf" || low == "nan" || infNan.MatchString(t) ||
		(strings.Contains(t, "_") && pyNumber.MatchString(t)) || timestamp.MatchString(t) {
		return nil, fail("line %d: %s is ambiguous across YAML parsers; quote it if you mean the string", line, pyRepr(t))
	}
	if sexagesimal.MatchString(t) {
		return nil, fail("line %d: %s is a YAML 1.1 sexagesimal; quote it if you mean the string", line, pyRepr(t))
	}
	sign, digits := splitSign(t)
	switch {
	case hexRe.MatchString(t):
		return parseInt(sign, digits[2:], 16, t, line)
	case binRe.MatchString(t):
		return parseInt(sign, digits[2:], 2, t, line)
	case octRe.MatchString(t):
		return parseInt(sign, digits, 8, t, line)
	case decRe.MatchString(t):
		return parseInt(sign, digits, 10, t, line)
	case floatRe.MatchString(t):
		f, err := strconv.ParseFloat(t, 64)
		if err != nil {
			return nil, fail("line %d: %s is not a number this reader takes", line, pyRepr(t))
		}
		return f, nil
	}
	if strings.ContainsAny(t, "[]{}") {
		return nil, fail("line %d: unbalanced or stray bracket in value: %s", line, pyRepr(t))
	}
	return t, nil
}

// splitFlow splits a flow collection body on commas at nesting depth 0.
func splitFlow(body string, line int) ([]string, error) {
	var parts []string
	var buf strings.Builder
	depth, escaped := 0, false
	var quote rune
	for _, ch := range body {
		if quote != 0 {
			buf.WriteRune(ch)
			switch {
			case escaped:
				escaped = false
			case ch == '\\' && quote == '"':
				escaped = true
			case ch == quote:
				quote = 0
			}
			continue
		}
		switch {
		case ch == '"' || ch == '\'':
			quote = ch
			buf.WriteRune(ch)
		case ch == '[' || ch == '{':
			depth++
			buf.WriteRune(ch)
		case ch == ']' || ch == '}':
			depth--
			buf.WriteRune(ch)
		case ch == ',' && depth == 0:
			parts = append(parts, buf.String())
			buf.Reset()
		default:
			buf.WriteRune(ch)
		}
	}
	if quote != 0 {
		return nil, fail("line %d: unterminated quote in flow collection: %s", line, pyRepr(body))
	}
	if depth != 0 {
		return nil, fail("line %d: unbalanced brackets in flow collection: %s", line, pyRepr(body))
	}
	if tail := buf.String(); strip(tail) != "" || len(parts) > 0 {
		parts = append(parts, tail)
	}
	for i := range parts {
		parts[i] = strip(parts[i])
	}
	return parts, nil
}

func flow(t string, line int) (any, error) {
	inner := t[1 : len(t)-1]
	items, err := splitFlow(inner, line)
	if err != nil {
		return nil, err
	}
	if t[0] == '[' {
		out := []any{}
		for _, p := range items {
			if p == "" {
				continue
			}
			v, err := scalar(p, line)
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
		return out, nil
	}
	out := map[string]any{}
	for _, item := range items {
		if item == "" {
			continue
		}
		k, v, ok := splitKey(item)
		if !ok {
			return nil, fail("line %d: flow map entry is not 'key: value': %s", line, pyRepr(item))
		}
		kk, err := key(k, line)
		if err != nil {
			return nil, err
		}
		if out[kk], err = scalar(strip(v), line); err != nil {
			return nil, err
		}
	}
	return out, nil
}

// splitKey splits 'key: value' at the first structural colon: one followed by whitespace or at the end. Colons inside quotes never separate.
func splitKey(item string) (string, string, bool) {
	var quote byte
	escaped := false
	for i := 0; i < len(item); i++ {
		ch := item[i]
		if quote != 0 {
			switch {
			case escaped:
				escaped = false
			case ch == '\\' && quote == '"':
				escaped = true
			case ch == quote:
				quote = 0
			}
			continue
		}
		if ch == '"' || ch == '\'' {
			quote = ch
		} else if ch == ':' && (i+1 == len(item) || item[i+1] == ' ' || item[i+1] == '\t') {
			return item[:i], item[i+1:], true
		}
	}
	return "", "", false
}

// stripComment removes a trailing comment: a # starts one only at the start of the line or after whitespace.
func stripComment(line string) string {
	var out strings.Builder
	var quote byte
	escaped := false
	for i := 0; i < len(line); i++ {
		ch := line[i]
		switch {
		case quote != 0:
			out.WriteByte(ch)
			switch {
			case escaped:
				escaped = false
			case ch == '\\' && quote == '"':
				escaped = true
			case ch == quote:
				quote = 0
			}
		case ch == '"' || ch == '\'':
			quote = ch
			out.WriteByte(ch)
		case ch == '#' && (i == 0 || line[i-1] == ' ' || line[i-1] == '\t'):
			return strings.TrimRightFunc(out.String(), unicode.IsSpace)
		default:
			out.WriteByte(ch)
		}
	}
	return strings.TrimRightFunc(out.String(), unicode.IsSpace)
}

type rowT struct {
	n, indent int
	body      string
}

func rowsOf(text string) ([]rowT, error) {
	var out []rowT
	emitted := false
	for i, raw := range textsafe.SplitLines(text) {
		n := i + 1
		lead := raw[:len(raw)-len(strings.TrimLeftFunc(raw, unicode.IsSpace))]
		if strings.Contains(lead, "\t") {
			return nil, fail("line %d: tab used for indentation; YAML forbids it", n)
		}
		if strings.HasPrefix(strings.TrimLeftFunc(raw, unicode.IsSpace), "#") {
			continue
		}
		line := stripComment(raw)
		stripped := strip(line)
		if stripped == "" {
			continue
		}
		if strings.HasPrefix(stripped, "---") {
			if emitted {
				return nil, fail("line %d: multiple YAML documents; ocp reads exactly one", n)
			}
			continue
		}
		if stripped == "..." {
			continue
		}
		emitted = true
		out = append(out, rowT{n, utf8.RuneCountInString(line) - utf8.RuneCountInString(strings.TrimLeftFunc(line, unicode.IsSpace)), stripped})
	}
	return out, nil
}

// outsideQuotes is body with its quoted scalars removed: what is inside quotes is text whatever it holds.
func outsideQuotes(body string) string {
	var out strings.Builder
	var quote rune
	escaped := false
	for _, ch := range body {
		if quote != 0 {
			switch {
			case escaped:
				escaped = false
			case ch == '\\' && quote == '"':
				escaped = true
			case ch == quote:
				quote = 0
			}
		} else if ch == '"' || ch == '\'' {
			quote = ch
		} else {
			out.WriteRune(ch)
		}
	}
	return out.String()
}

func check(n int, body string) error {
	outside := outsideQuotes(body)
	for _, bad := range unsupported {
		if strings.Contains(outside, bad) {
			return fail("line %d: bundled YAML parser does not support %s. Run ocp under a python3 that has PyYAML, or simplify the syntax.", n, pyRepr(bad))
		}
	}
	return nil
}

// Parse reads one YAML document; an empty one is an empty map.
func Parse(text string) (any, error) {
	rows, err := rowsOf(text)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return map[string]any{}, nil
	}
	v, idx, err := block(rows, 0, rows[0].indent)
	if err != nil {
		return nil, err
	}
	if idx != len(rows) {
		return nil, fail("line %d: inconsistent indentation", rows[idx].n)
	}
	if v == nil {
		return map[string]any{}, nil
	}
	return v, nil
}

func isItem(body string) bool { return strings.HasPrefix(body, "- ") || body == "-" }

func block(rows []rowT, i, indent int) (any, int, error) {
	if i >= len(rows) {
		return nil, i, nil
	}
	if isItem(rows[i].body) {
		return seq(rows, i, indent)
	}
	return mapOf(rows, i, indent)
}

func seq(rows []rowT, i, indent int) (any, int, error) {
	out := []any{}
	for i < len(rows) {
		n, ind, body := rows[i].n, rows[i].indent, rows[i].body
		if ind < indent || !isItem(body) {
			break
		}
		if ind > indent {
			return nil, i, fail("line %d: list item indentation does not line up", n)
		}
		rest := ""
		if strings.HasPrefix(body, "- ") {
			rest = strip(body[2:])
		}
		if strings.HasPrefix(rest, "[") || strings.HasPrefix(rest, "{") {
			v, err := scalar(rest, n)
			if err != nil {
				return nil, i, err
			}
			out = append(out, v)
			i++
			continue
		}
		hasKey := false
		if rest != "" {
			_, _, hasKey = splitKey(rest)
		}
		switch {
		case hasKey:
			// The item is a map: its first line is a map row at the item's own indent, then the more-indented lines under it.
			synth := []rowT{{n, indent + 2, rest}}
			j := i + 1
			for j < len(rows) && rows[j].indent > indent {
				synth = append(synth, rows[j])
				j++
			}
			item, k, err := mapOf(synth, 0, indent+2)
			if err != nil {
				return nil, i, err
			}
			if k != len(synth) {
				return nil, i, fail("line %d: could not parse list item", synth[k].n)
			}
			out = append(out, item)
			i = j
		case rest != "":
			if err := check(n, rest); err != nil {
				return nil, i, err
			}
			v, err := scalar(rest, n)
			if err != nil {
				return nil, i, err
			}
			out = append(out, v)
			i++
		default:
			var child any
			if i+1 < len(rows) {
				var err error
				if child, i, err = block(rows, i+1, rows[i+1].indent); err != nil {
					return nil, i, err
				}
			} else {
				i++
			}
			out = append(out, child)
		}
	}
	return out, i, nil
}

func mapOf(rows []rowT, i, indent int) (any, int, error) {
	out := map[string]any{}
	for i < len(rows) {
		n, ind, body := rows[i].n, rows[i].indent, rows[i].body
		if ind < indent || isItem(body) {
			break
		}
		if ind > indent {
			return nil, i, fail("line %d: unexpected indentation", n)
		}
		k, rest, ok := splitKey(body)
		if !ok {
			return nil, i, fail("line %d: expected 'key: value' but found %s", n, pyRepr(body))
		}
		kk, err := key(k, n)
		if err != nil {
			return nil, i, err
		}
		rest = strip(rest)
		switch {
		case rest != "":
			if err := check(n, rest); err != nil {
				return nil, i, err
			}
			if out[kk], err = scalar(rest, n); err != nil {
				return nil, i, err
			}
			i++
		case i+1 < len(rows) && rows[i+1].indent > indent:
			if out[kk], i, err = block(rows, i+1, rows[i+1].indent); err != nil {
				return nil, i, err
			}
		case i+1 < len(rows) && rows[i+1].indent == indent && isItem(rows[i+1].body):
			if out[kk], i, err = seq(rows, i+1, indent); err != nil {
				return nil, i, err
			}
		default:
			out[kk] = nil
			i++
		}
	}
	return out, i, nil
}

// IsPlainText is whether name is read back as itself wherever ocp writes one unquoted: as a block key, as a value, as a list
// item and as a flow key.
func IsPlainText(name string) bool {
	shapes := []struct {
		src  string
		want any
	}{
		{name + ": 1\n", map[string]any{name: int64(1)}},
		{"k: " + name + "\n", map[string]any{"k": name}},
		{"k: [" + name + "]\n", map[string]any{"k": []any{name}}},
		{"k: { " + name + ": 1 }\n", map[string]any{"k": map[string]any{name: int64(1)}}},
	}
	for _, s := range shapes {
		got, err := Parse(s.src)
		if err != nil || !reflect.DeepEqual(got, s.want) {
			return false
		}
	}
	return true
}

func readsBackAsOther(text string) bool {
	v, err := scalar(text, 0)
	if err != nil {
		return true
	}
	s, ok := v.(string)
	return !ok || s != text
}

// floatRepr is Python's repr of a float.
func floatRepr(f float64) string {
	e := strconv.FormatFloat(f, 'e', -1, 64)
	mant, exps, _ := strings.Cut(e, "e")
	exp, _ := strconv.Atoi(exps)
	if exp >= -4 && exp < 16 {
		s := strconv.FormatFloat(f, 'f', -1, 64)
		if !strings.ContainsAny(s, ".") {
			s += ".0"
		}
		return s
	}
	sign := "+"
	if exp < 0 {
		sign, exp = "-", -exp
	}
	return fmt.Sprintf("%se%s%02d", mant, sign, exp)
}

// floatText is a float as YAML 1.1 reads it back as a float: an exponent only after a decimal point.
func floatText(f float64) (string, error) {
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return "", fail("dump does not write %v: no form both parsers read back", f)
	}
	text := floatRepr(f)
	mant, exp, has := strings.Cut(text, "e")
	if strings.Contains(mant, ".") || !has {
		return text, nil
	}
	return mant + ".0e" + exp, nil
}

func quoted(text string) string {
	return `"` + strings.ReplaceAll(strings.ReplaceAll(text, `\`, `\\`), `"`, `\"`) + `"`
}

// RefuseUnsafeValues refuses a parsed map that carries a value Dump would have refused to write: the read side of the same rule.
// Only top-level string values are checked.
func RefuseUnsafeValues(data map[string]any, source string) error {
	for k, v := range data {
		s, ok := v.(string)
		if !ok {
			continue
		}
		if r, bad := firstUnsafe(s); bad {
			return fail("%s: key %s carries a control character (0x%x) - refused, not read as though it were not there", source, pyRepr(k), r)
		}
	}
	return nil
}

func firstUnsafe(s string) (rune, bool) {
	for _, r := range s {
		if textsafe.HasUnsafe(string(r)) {
			return r, true
		}
	}
	return 0, false
}

// Pair is one key and value of a map Dump writes, in order.
type Pair struct {
	Key string
	Val any
}

func normal(v any) any {
	switch x := v.(type) {
	case int:
		return int64(x)
	case int32:
		return int64(x)
	}
	return v
}

// Dump serializes a shallow config map back to YAML: a flat map of scalars, nothing nested. Every line is read back through
// Parse before it is kept, so a value the read path would refuse or change is an error here, at the write.
func Dump(data []Pair) (string, error) {
	var lines []string
	for _, p := range data {
		value := normal(p.Val)
		switch value.(type) {
		case map[string]any, []any:
			return "", fail("dump does not write nested values (key %s)", pyRepr(p.Key))
		}
		var candidates []string
		switch v := value.(type) {
		case nil:
			candidates = []string{"null"}
		case bool:
			candidates = []string{map[bool]string{true: "true", false: "false"}[v]}
		case int64:
			candidates = []string{strconv.FormatInt(v, 10)}
		case float64:
			t, err := floatText(v)
			if err != nil {
				return "", err
			}
			candidates = []string{t}
		default:
			s := fmt.Sprint(v)
			value = s
			if _, bad := firstUnsafe(s); bad {
				return "", fail("dump does not write control characters (key %s)", pyRepr(p.Key))
			}
			low := strings.ToLower(s)
			needs := strip(s) != s || s == "" || yTrue[low] || yFalse[low] || low == "null" || low == "~" ||
				strings.ContainsRune("-?!%@`=", rune(s[0])) || hasAnyPrefix(s, unsupported) ||
				strings.ContainsAny(s, `:#[]{},&*|>'"`) || readsBackAsOther(s)
			if needs {
				candidates = []string{quoted(s)}
			} else {
				candidates = []string{s, quoted(s)}
			}
		}
		wrote := false
		for _, rendered := range candidates {
			line := p.Key + ": " + rendered
			back, err := Parse(line + "\n")
			if err != nil {
				continue
			}
			if m, ok := back.(map[string]any); ok && reflect.DeepEqual(normal(m[p.Key]), value) {
				lines = append(lines, line)
				wrote = true
				break
			}
		}
		if !wrote {
			return "", fail("dump cannot write %s: the parser does not read %s back as %v", pyRepr(p.Key), pyRepr(candidates[len(candidates)-1]), value)
		}
	}
	return strings.Join(lines, "\n") + "\n", nil
}

func hasAnyPrefix(s string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}
