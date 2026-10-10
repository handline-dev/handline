package claude

import (
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
	"unicode/utf16"

	"github.com/handline/handline/go/internal/yamlcompat"
)

// Object is a JSON object that keeps its keys in order, as a Python dict does: the shared settings hold the founder's
// own settings too, and a rewrite must not reorder them.
type Object struct {
	keys []string
	vals map[string]any
}

// NewObject is an empty Object.
func NewObject() *Object { return &Object{vals: map[string]any{}} }

// Get is the value under k and whether there is one.
func (o *Object) Get(k string) (any, bool) { v, ok := o.vals[k]; return v, ok }

// Set puts v under k, at the end when k is new.
func (o *Object) Set(k string, v any) {
	if _, has := o.vals[k]; !has {
		o.keys = append(o.keys, k)
	}
	o.vals[k] = v
}

// Delete takes k out.
func (o *Object) Delete(k string) {
	if _, has := o.vals[k]; !has {
		return
	}
	delete(o.vals, k)
	for i, x := range o.keys {
		if x == k {
			o.keys = append(o.keys[:i], o.keys[i+1:]...)
			return
		}
	}
}

// Keys are the keys in order.
func (o *Object) Keys() []string { return append([]string(nil), o.keys...) }

// Len is how many keys there are.
func (o *Object) Len() int { return len(o.keys) }

// setDefault is dict.setdefault: the value under k, put there first when there is none.
func (o *Object) setDefault(k string, v any) any {
	if cur, ok := o.vals[k]; ok {
		return cur
	}
	o.Set(k, v)
	return v
}

// block is parent[key] as an object, replacing anything else with an empty one (claude_hooks._block).
func (o *Object) block(k string) *Object {
	if b, ok := o.vals[k].(*Object); ok {
		return b
	}
	b := NewObject()
	o.Set(k, b)
	return b
}

// Decode reads one JSON value, objects as *Object and numbers as json.Number.
func Decode(text string) (any, error) {
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
	delim, ok := tok.(json.Delim)
	if !ok {
		return tok, nil
	}
	switch delim {
	case '{':
		o := NewObject()
		for d.More() {
			k, err := d.Token()
			if err != nil {
				return nil, err
			}
			v, err := readValue(d)
			if err != nil {
				return nil, err
			}
			o.Set(k.(string), v)
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

// Dumps is json.dumps(v) with indent spaces per level (0: on one line with ", " and ": ") and, when ensureASCII, every
// non-ASCII character escaped. v holds what Decode gives, plus string, bool, nil, int, []string and *Object.
func Dumps(v any, indent int, ensureASCII bool) string {
	var b strings.Builder
	write(&b, v, indent, ensureASCII, "")
	return b.String()
}

func write(b *strings.Builder, v any, indent int, ascii bool, at string) {
	inner, itemSep, open, close := "", ", ", "", ""
	if indent > 0 {
		inner = at + strings.Repeat(" ", indent)
		itemSep, open, close = ",\n"+inner, "\n"+inner, "\n"+at
	}
	switch x := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		b.WriteString(map[bool]string{true: "true", false: "false"}[x])
	case int:
		b.WriteString(strconv.Itoa(x))
	case string:
		writeString(b, x, ascii)
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
	case []string:
		items := make([]any, len(x))
		for i, s := range x {
			items[i] = s
		}
		write(b, items, indent, ascii, at)
	case []any:
		if len(x) == 0 {
			b.WriteString("[]")
			return
		}
		b.WriteString("[" + open)
		for i, e := range x {
			if i > 0 {
				b.WriteString(itemSep)
			}
			write(b, e, indent, ascii, inner)
		}
		b.WriteString(close + "]")
	case *Object:
		if x.Len() == 0 {
			b.WriteString("{}")
			return
		}
		b.WriteString("{" + open)
		for i, k := range x.keys {
			if i > 0 {
				b.WriteString(itemSep)
			}
			writeString(b, k, ascii)
			b.WriteString(": ")
			write(b, x.vals[k], indent, ascii, inner)
		}
		b.WriteString(close + "}")
	default:
		panic(fmt.Sprintf("claude.Dumps: %T", v))
	}
}

// writeString is a JSON string as Python's json module writes it.
func writeString(b *strings.Builder, s string, ascii bool) {
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			switch {
			case r < 0x20 || (ascii && r == 0x7f):
				fmt.Fprintf(b, `\u%04x`, r)
			case ascii && r > 0x7f && r > 0xffff:
				hi, lo := utf16.EncodeRune(r)
				fmt.Fprintf(b, `\u%04x\u%04x`, hi, lo)
			case ascii && r > 0x7f:
				fmt.Fprintf(b, `\u%04x`, r)
			default:
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}
