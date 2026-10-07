package team

import (
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"

	"github.com/handline/handline/go/internal/yamlcompat"
)

// jobj is a JSON object that keeps its keys in order, as a Python dict does: an mcp_config is rewritten, not reordered.
type jobj struct {
	keys []string
	vals map[string]any
}

func newJobj() *jobj { return &jobj{vals: map[string]any{}} }

func (o *jobj) set(k string, v any) {
	if _, has := o.vals[k]; !has {
		o.keys = append(o.keys, k)
	}
	o.vals[k] = v
}

func (o *jobj) del(k string) {
	if _, has := o.vals[k]; !has {
		return
	}
	delete(o.vals, k)
	for i, x := range o.keys {
		if x == k {
			o.keys = append(o.keys[:i], o.keys[i+1:]...)
			break
		}
	}
}

func decodeOrdered(text string) (any, error) {
	d := json.NewDecoder(strings.NewReader(text))
	d.UseNumber()
	v, err := readValue(d)
	if err != nil {
		return nil, err
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, fmt.Errorf("extra data after the JSON value")
	}
	return v, nil
}

func readValue(d *json.Decoder) (any, error) {
	tok, err := d.Token()
	if err != nil {
		return nil, err
	}
	delim, isDelim := tok.(json.Delim)
	if !isDelim {
		return tok, nil
	}
	switch delim {
	case '{':
		o := newJobj()
		for d.More() {
			kt, err := d.Token()
			if err != nil {
				return nil, err
			}
			v, err := readValue(d)
			if err != nil {
				return nil, err
			}
			o.set(kt.(string), v)
		}
		_, err := d.Token()
		return o, err
	case '[':
		out := []any{}
		for d.More() {
			v, err := readValue(d)
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
		_, err := d.Token()
		return out, err
	}
	return nil, fmt.Errorf("unexpected %v", delim)
}

// encodeCompact is json.dumps(v, separators=(",", ":")): ASCII only, keys in order, floats as Python writes them.
func encodeCompact(v any) string {
	var b strings.Builder
	writeValue(&b, v)
	return b.String()
}

func writeValue(b *strings.Builder, v any) {
	switch x := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		b.WriteString(strconv.FormatBool(x))
	case string:
		writeString(b, x)
	case json.Number:
		s := string(x)
		if strings.ContainsAny(s, ".eE") {
			f, _ := strconv.ParseFloat(s, 64)
			b.WriteString(yamlcompat.FloatRepr(f))
		} else if s == "-0" {
			b.WriteString("0")
		} else {
			b.WriteString(s)
		}
	case []any:
		b.WriteByte('[')
		for i, e := range x {
			if i > 0 {
				b.WriteByte(',')
			}
			writeValue(b, e)
		}
		b.WriteByte(']')
	case *jobj:
		b.WriteByte('{')
		for i, k := range x.keys {
			if i > 0 {
				b.WriteByte(',')
			}
			writeString(b, k)
			b.WriteByte(':')
			writeValue(b, x.vals[k])
		}
		b.WriteByte('}')
	}
}

func writeString(b *strings.Builder, s string) {
	b.WriteByte('"')
	for _, r := range s {
		switch {
		case r == '"':
			b.WriteString(`\"`)
		case r == '\\':
			b.WriteString(`\\`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r == '\b':
			b.WriteString(`\b`)
		case r == '\f':
			b.WriteString(`\f`)
		case r < 0x20:
			fmt.Fprintf(b, `\u%04x`, r)
		case r < 0x80:
			b.WriteRune(r)
		case r > 0xffff:
			hi, lo := utf16.EncodeRune(r)
			fmt.Fprintf(b, `\u%04x\u%04x`, hi, lo)
		default:
			fmt.Fprintf(b, `\u%04x`, r)
		}
	}
	b.WriteByte('"')
}

var shlexSafe = regexp.MustCompile(`^[\p{L}\p{N}_@%+=:,./-]+$`)

// shlexQuote is shlex.quote.
func shlexQuote(s string) string {
	if s == "" {
		return "''"
	}
	if shlexSafe.MatchString(s) {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
}

// encodeIndent is json.dumps(v, indent=2): items on their own lines, ": " after a key, ASCII only.
func encodeIndent(v any) string {
	var b strings.Builder
	writeIndented(&b, v, 0)
	return b.String()
}

func writeIndented(b *strings.Builder, v any, depth int) {
	pad := strings.Repeat("  ", depth+1)
	end := strings.Repeat("  ", depth)
	switch x := v.(type) {
	case []any:
		if len(x) == 0 {
			b.WriteString("[]")
			return
		}
		b.WriteString("[\n")
		for i, e := range x {
			b.WriteString(pad)
			writeIndented(b, e, depth+1)
			if i < len(x)-1 {
				b.WriteByte(',')
			}
			b.WriteByte('\n')
		}
		b.WriteString(end + "]")
	case *jobj:
		if len(x.keys) == 0 {
			b.WriteString("{}")
			return
		}
		b.WriteString("{\n")
		for i, k := range x.keys {
			b.WriteString(pad)
			writeString(b, k)
			b.WriteString(": ")
			writeIndented(b, x.vals[k], depth+1)
			if i < len(x.keys)-1 {
				b.WriteByte(',')
			}
			b.WriteByte('\n')
		}
		b.WriteString(end + "}")
	default:
		writeValue(b, v)
	}
}

// encodeSorted is json.dumps(v, sort_keys=True, separators=(",", ":")).
func encodeSorted(v any) string {
	var b strings.Builder
	writeSorted(&b, v)
	return b.String()
}

func writeSorted(b *strings.Builder, v any) {
	switch x := v.(type) {
	case []any:
		b.WriteByte('[')
		for i, e := range x {
			if i > 0 {
				b.WriteByte(',')
			}
			writeSorted(b, e)
		}
		b.WriteByte(']')
	case *jobj:
		keys := append([]string(nil), x.keys...)
		sort.Strings(keys)
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			writeString(b, k)
			b.WriteByte(':')
			writeSorted(b, x.vals[k])
		}
		b.WriteByte('}')
	default:
		writeValue(b, v)
	}
}

func jsonInt(n int64) json.Number { return json.Number(strconv.FormatInt(n, 10)) }
