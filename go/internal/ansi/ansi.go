// Package ansi reads one screen row with SGR escape codes (what `tmux capture-pane -e` writes) as its text and style runs
// (handline/infra/ansi.py), for the `styled` terminal format.
//
// The text is the row without its trailing blanks, as the `text` format has it. A run is [from, to) over the row's code points;
// blank cells that carry a background count one each past the end of the text. Only SGR is read: every other escape sequence and
// control character is dropped.
package ansi

import (
	"fmt"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

var (
	esc    = regexp.MustCompile("\x1b(?:\\[([0-?]*)[ -/]*([@-~])|\\][^\x07\x1b]*(?:\x07|\x1b\\\\)|[\x20-\x2f]*[\x30-\x7e])")
	sgrArg = regexp.MustCompile(`^[0-9;:]*$`)
	colSp  = regexp.MustCompile(`([34]8):2::`)
)

// Style is the attributes of a run: bold, dim, italic, underline, inverse, strike as true, fg and bg as a palette index (int)
// or "#rrggbb".
type Style map[string]any

// Span is one run of cells with the same style.
type Span struct {
	From, To int
	Style    Style
}

// Map is the span as the protocol writes it.
func (s Span) Map() map[string]any {
	return map[string]any{"from": s.From, "to": s.To, "style": map[string]any(s.Style)}
}

var flags = map[int]struct {
	key string
	on  bool
}{1: {"bold", true}, 2: {"dim", true}, 3: {"italic", true}, 4: {"underline", true}, 7: {"inverse", true}, 9: {"strike", true},
	22: {"bold", false}, 23: {"italic", false}, 24: {"underline", false}, 27: {"inverse", false}, 29: {"strike", false}}

// extended is the colour that follows a 38 or 48 at args[i], and the index after it; nil for none.
func extended(args []int, i int) (any, int) {
	if i+1 < len(args) && args[i+1] == 5 && i+2 < len(args) {
		if c := args[i+2]; c >= 0 && c <= 255 {
			return c, i + 3
		}
		return nil, i + 3
	}
	if i+1 < len(args) && args[i+1] == 2 && i+4 < len(args) {
		r, g, b := args[i+2], args[i+3], args[i+4]
		ok := true
		for _, c := range []int{r, g, b} {
			ok = ok && c >= 0 && c <= 255
		}
		if ok {
			return fmt.Sprintf("#%02x%02x%02x", r, g, b), i + 5
		}
		return nil, i + 5
	}
	return nil, len(args)
}

func atoi(s string) int {
	if s == "" {
		return 0
	}
	n, _ := strconv.Atoi(s)
	return n
}

// sgr is style after the SGR parameters params, as a copy.
func sgr(style Style, params string) Style {
	out := Style{}
	for k, v := range style {
		out[k] = v
	}
	params = colSp.ReplaceAllString(params, "$1:2:") // 38:2::r:g:b has an empty colour-space id after the 2
	var args []int
	for _, p := range strings.Split(strings.ReplaceAll(params, ":", ";"), ";") {
		args = append(args, atoi(p))
	}
	for i := 0; i < len(args); {
		n := args[i]
		i++
		switch {
		case n == 0:
			out = Style{}
		case flags[n].key != "":
			f := flags[n]
			if f.on {
				out[f.key] = true
			} else {
				delete(out, f.key)
				if n == 22 {
					delete(out, "dim")
				}
			}
		case 30 <= n && n <= 37:
			out["fg"] = n - 30
		case 90 <= n && n <= 97:
			out["fg"] = n - 90 + 8
		case 40 <= n && n <= 47:
			out["bg"] = n - 40
		case 100 <= n && n <= 107:
			out["bg"] = n - 100 + 8
		case n == 38 || n == 48:
			var c any
			c, i = extended(args, i-1)
			if c != nil {
				key := "fg"
				if n == 48 {
					key = "bg"
				}
				out[key] = c
			}
		case n == 39:
			delete(out, "fg")
		case n == 49:
			delete(out, "bg")
		}
	}
	return out
}

type cell struct {
	r     rune
	style Style
}

func printable(r rune) bool { return r >= ' ' && r != 0x7f }

func cells(s string, style Style) []cell {
	var out []cell
	for _, r := range s {
		if printable(r) {
			out = append(out, cell{r, style})
		}
	}
	return out
}

// Parse is the text and the spans of one row of raw.
func Parse(raw string) (string, []Span) {
	var all []cell
	style := Style{}
	pos := 0
	for _, m := range esc.FindAllStringSubmatchIndex(raw, -1) {
		all = append(all, cells(raw[pos:m[0]], style)...)
		pos = m[1]
		if m[4] >= 0 && raw[m[4]:m[5]] == "m" && m[2] >= 0 && sgrArg.MatchString(raw[m[2]:m[3]]) {
			p := raw[m[2]:m[3]]
			if p == "" {
				p = "0"
			}
			style = sgr(style, p)
		}
	}
	all = append(all, cells(raw[pos:], style)...)
	var sb strings.Builder
	for _, c := range all {
		sb.WriteRune(c.r)
	}
	text := strings.TrimRightFunc(sb.String(), unicode.IsSpace)
	for len(all) > 0 && all[len(all)-1].r == ' ' && len(all[len(all)-1].style) == 0 {
		all = all[:len(all)-1]
	}
	var spans []Span
	for x, c := range all {
		if len(c.style) == 0 {
			continue
		}
		if n := len(spans); n > 0 && spans[n-1].To == x && reflect.DeepEqual(spans[n-1].Style, c.style) {
			spans[n-1].To = x + 1
		} else {
			spans = append(spans, Span{x, x + 1, c.style})
		}
	}
	return text, spans
}
