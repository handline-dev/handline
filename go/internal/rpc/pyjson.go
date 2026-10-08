package rpc

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"unicode/utf16"
)

// Pairs is an object whose keys go out in the order given, as a Python dict's insertion order does.
type Pairs []Pair

// Pair is one key and value of Pairs.
type Pair struct {
	K string
	V any
}

// PyJSON is v as Python's json.dumps writes it (", " and ": " between items, keys in the order given by sortKeys,
// non-ASCII escaped when ensureASCII): text the Python daemon reads back and compares as it wrote it, such as a
// write's params in rpc_replies. v holds nil, bool, string, json.Number, int, int64, float64, []string, []any, map[string]any.
func PyJSON(v any, sortKeys, ensureASCII bool) string {
	var b strings.Builder
	pyWrite(&b, v, sortKeys, ensureASCII)
	return b.String()
}

func pyWrite(b *strings.Builder, v any, sortKeys, ascii bool) {
	switch t := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if t {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case string:
		pyString(b, t, ascii)
	case json.Number:
		b.WriteString(string(t))
	case int:
		fmt.Fprint(b, t)
	case int64:
		fmt.Fprint(b, t)
	case float64:
		out, _ := json.Marshal(t)
		b.Write(out)
	case []string:
		items := make([]any, len(t))
		for i, x := range t {
			items[i] = x
		}
		pyWrite(b, items, sortKeys, ascii)
	case []any:
		b.WriteByte('[')
		for i, x := range t {
			if i > 0 {
				b.WriteString(", ")
			}
			pyWrite(b, x, sortKeys, ascii)
		}
		b.WriteByte(']')
	case Pairs:
		b.WriteByte('{')
		for i, e := range t {
			if i > 0 {
				b.WriteString(", ")
			}
			pyString(b, e.K, ascii)
			b.WriteString(": ")
			pyWrite(b, e.V, sortKeys, ascii)
		}
		b.WriteByte('}')
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		if sortKeys {
			sort.Strings(keys)
		}
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteString(", ")
			}
			pyString(b, k, ascii)
			b.WriteString(": ")
			pyWrite(b, t[k], sortKeys, ascii)
		}
		b.WriteByte('}')
	default:
		pyString(b, fmt.Sprint(t), ascii)
	}
}

func pyString(b *strings.Builder, s string, ascii bool) {
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
		case r < 0x20 || (ascii && r > 0x7e):
			if r >= 0x10000 {
				hi, lo := utf16.EncodeRune(r)
				fmt.Fprintf(b, `\u%04x\u%04x`, hi, lo)
			} else {
				fmt.Fprintf(b, `\u%04x`, r)
			}
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
}
