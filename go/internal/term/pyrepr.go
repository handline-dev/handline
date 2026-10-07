package term

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
)

// pyRepr is Python's repr of a plain string, for the messages that quote one.
func pyRepr(s string) string {
	q := "'"
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		q = `"`
	}
	var b strings.Builder
	b.WriteString(q)
	for _, r := range s {
		switch {
		case r == '\\':
			b.WriteString(`\\`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case string(r) == q:
			b.WriteString(`\` + q)
		case r < 0x20 || r == 0x7f:
			fmt.Fprintf(&b, `\x%02x`, r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteString(q)
	return b.String()
}

func pyList(keys []string) string {
	parts := make([]string, len(keys))
	for i, k := range keys {
		parts[i] = pyRepr(k)
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

func pyInts(xs []int) string {
	parts := make([]string, len(xs))
	for i, x := range xs {
		parts[i] = strconv.Itoa(x)
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

func sortInts(xs []int) { sort.Ints(xs) }
