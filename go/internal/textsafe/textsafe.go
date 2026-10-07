// Package textsafe is what a terminal would obey and how to show text without it (handline/infra/textsafe.py): text that
// someone else wrote is drawn with every control taken out, and a stored value holding one is refused, not rewritten.
// wrap and cwidth are the screens' and are not here.
package textsafe

import (
	"regexp"
	"strings"
	"unicode"
)

var (
	osc = regexp.MustCompile("\x1b\\][^\a\x1b]*(?:\a|\x1b\\\\)?")
	csi = regexp.MustCompile("\x1b\\[[0-?]*[ -/]*[@-~]")
)

// isControl is a single-byte control a terminal obeys instead of draws: C0 but tab and newline, DEL and C1.
func isControl(r rune) bool {
	return (r >= 0 && r < 0x09) || (r >= 0x0b && r < 0x20) || (r >= 0x7f && r < 0xa0)
}

// HasUnsafe is whether value holds a control, a tab, a newline, U+2028 or U+2029: it cannot be stored and handed on as it is.
func HasUnsafe(value string) bool {
	for _, r := range value {
		if isControl(r) || r == '\t' || r == '\n' || r == ' ' || r == ' ' {
			return true
		}
	}
	return false
}

// SplitLines is str.splitlines: a break is any of \n \r \r\n \v \f the FS-US separators, NEL, U+2028 and U+2029.
func SplitLines(s string) []string {
	var out []string
	var cur strings.Builder
	rs := []rune(s)
	for i := 0; i < len(rs); i++ {
		r := rs[i]
		switch r {
		case '\n', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
		case '\r':
			if i+1 < len(rs) && rs[i+1] == '\n' {
				i++
			}
		default:
			cur.WriteRune(r)
			continue
		}
		out = append(out, cur.String())
		cur.Reset()
	}
	if cur.Len() > 0 {
		out = append(out, cur.String())
	}
	return out
}

func expandTabs(line string, tabs int) string {
	var b strings.Builder
	col := 0
	for _, r := range line {
		switch r {
		case '\t':
			n := tabs - col%tabs
			b.WriteString(strings.Repeat(" ", n))
			col += n
		default:
			b.WriteRune(r)
			col++
		}
	}
	return b.String()
}

// Drawable is text as lines of plain text and nothing a terminal would obey. A tab becomes tabs spaces; tabs 0 leaves it.
func Drawable(text string, tabs int) []string {
	text = csi.ReplaceAllString(osc.ReplaceAllString(text, ""), "")
	lines := SplitLines(text)
	if len(lines) == 0 {
		lines = []string{""}
	}
	for i, ln := range lines {
		if tabs > 0 {
			ln = expandTabs(ln, tabs)
		}
		lines[i] = strings.Map(func(r rune) rune {
			if isControl(r) {
				return -1
			}
			return r
		}, ln)
	}
	return lines
}

// Text is value as one string that can be printed as it is: Drawable's lines joined by newlines, tabs kept.
func Text(value string) string { return strings.Join(Drawable(value, 0), "\n") }

// Oneline is value as a single line of printable text: a newline is a space and anything else unprintable is blanked.
func Oneline(value string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsPrint(r) {
			return r
		}
		return ' '
	}, strings.Join(Drawable(value, 4), " "))
}
