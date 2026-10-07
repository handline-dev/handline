package term

import (
	"regexp"
	"strconv"
	"strings"
)

// The composer's own box: two full-width '─' rules around one content row. How many rows sit below it varies by agent
// (measured across the fleet), so the triple is searched for rather than assumed to sit a fixed distance from the end.
var composerRuleRE = regexp.MustCompile(`^─{8,}$`)

// ComposerBox is the (top, bottom) row indices of the composer's rule/content/rule triple.
func ComposerBox(tail []string) (top, bottom int, ok bool) {
	for k := 3; k < 9; k++ {
		if len(tail) < k {
			break
		}
		top, bottom = len(tail)-k, len(tail)-k+2
		if composerRuleRE.MatchString(strings.TrimSpace(tail[top])) && composerRuleRE.MatchString(strings.TrimSpace(tail[bottom])) {
			return top, bottom, true
		}
	}
	return 0, 0, false
}

// ComposerShape is whether the tail holds a recognized composer box, never what is in it.
func ComposerShape(tail []string) bool { _, _, ok := ComposerBox(tail); return ok }

// ComposerText is what sits in the composer, whitespace-free, "" when it is empty; ok is false when no box is drawn. The box is
// the last two rules; its content may wrap over several rows.
func ComposerText(tail []string) (string, bool) {
	var rules []int
	for i, r := range tail {
		if composerRuleRE.MatchString(strings.TrimSpace(r)) {
			rules = append(rules, i)
		}
	}
	if len(rules) < 2 {
		return "", false
	}
	last, prev := rules[len(rules)-1], rules[len(rules)-2]
	if len(tail)-last > 6 || !(last-prev > 1 && last-prev <= 13) {
		return "", false
	}
	rows := make([]string, 0, last-prev-1)
	for _, r := range tail[prev+1 : last] {
		rows = append(rows, strings.TrimSpace(r))
	}
	rows[0] = strings.TrimLeft(rows[0], "❯")
	return strings.Join(strings.Fields(strings.Join(rows, "")), ""), true
}

// A scrolled box shows only part of a long text; less than this of it is not told from a person's draft.
const ownPart = 20

var pastedRE = regexp.MustCompile(`^\[Pastedtext#\d+\+(\d+)lines\]$`)

// IsTextTyped is whether the composer's content is the text the daemon typed: equal ignoring whitespace, a stretch of it (what
// a scrolled box shows, never longer than the text), or the placeholder a multi-line paste collapses to. Anything else, a person's
// draft or words of theirs typed after ours, is not the daemon's to send.
func IsTextTyped(box, typed string) bool {
	want := strings.Join(strings.Fields(typed), "")
	if box == "" || want == "" {
		return false
	}
	if box == want || (len([]rune(box)) >= ownPart && strings.Contains(want, box)) {
		return true
	}
	m := pastedRE.FindStringSubmatch(box)
	if m == nil || !strings.Contains(typed, "\n") {
		return false
	}
	n, _ := strconv.Atoi(m[1])
	c := strings.Count(typed, "\n")
	return n == c || n == c+1
}

// What claude prints as it exits, and what a shell shows after it: the old composer stays drawn above both, so a screen with a
// box on it is not enough to say a session is there.
const ExitBanner = "Resume this session with"

var shellPromptRE = regexp.MustCompile(`(?:^|\s)[%$#❯]$|^[a-z]+(?: [a-z]+)*> ?$`)

// ShellPromptIn is whether the bottom of this screen shows a shell, not a session: claude's exit banner, or a prompt (`%`, `$`,
// `#`, or zsh's continuation such as `if>`) in the last row below any composer box.
func ShellPromptIn(tail []string) bool {
	rows := make([]string, len(tail))
	for i, r := range tail {
		rows[i] = strings.TrimRight(r, " \t\r\n\f\v")
	}
	for len(rows) > 0 && strings.TrimSpace(rows[len(rows)-1]) == "" {
		rows = rows[:len(rows)-1]
	}
	if len(rows) == 0 {
		return false
	}
	below := rows
	if _, bottom, ok := ComposerBox(rows); ok {
		below = rows[bottom+1:]
	}
	last6 := below
	if len(last6) > 6 {
		last6 = last6[len(last6)-6:]
	}
	for _, r := range last6 {
		if strings.Contains(r, ExitBanner) {
			return true
		}
	}
	return len(below) > 0 && shellPromptRE.MatchString(strings.TrimSpace(below[len(below)-1]))
}

// Claude Code's hint row under the composer counts what still runs in the background as "<n> <noun>" segments between its mode
// and its key hints, e.g. "⏵⏵ auto mode on · 2 monitors · ← for agents · ↓ to manage" (measured live 2026-09-24).
var bgCountRE = regexp.MustCompile(`^\d+\s+[A-Za-z][A-Za-z ]*$`)

const bgHint = "to manage"

// FooterBackground is what the hint row under the composer counts, "" when it counts nothing; ok is false when no composer is
// drawn.
func FooterBackground(tail []string) (string, bool) {
	_, bottom, ok := ComposerBox(tail)
	if !ok {
		return "", false
	}
	for _, row := range tail[bottom+1:] {
		if !strings.Contains(row, bgHint) {
			continue
		}
		var found []string
		for _, p := range strings.Split(row, "·") {
			if p = strings.TrimSpace(p); bgCountRE.MatchString(p) {
				found = append(found, p)
			}
		}
		if len(found) == 0 {
			return "background tasks", true
		}
		return strings.Join(found, ", "), true
	}
	return "", true
}
