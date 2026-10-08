// Package session is handline/session as the daemon and the hooks use it: what is true of an agent's session right now (observe),
// what stands in the way of typing at it (blocked, waiting), the standing answers to its dialogs (autoaccept), and the guards on
// the commands it runs (shellguard, landgate, bgjobs).
package session

import (
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/paths"
)

var ocpRE = regexp.MustCompile(`(?:^|[\s;&|(])(?:\S*/)?(?:` + strings.Join(paths.Commands, "|") + `)\s`)
var heredocRE = regexp.MustCompile(`^<<(-?)[ \t]*(['"]?)([A-Za-z_][A-Za-z0-9_]*)`)

// GuardReason is why a Bash call is refused when a double-quoted ocp argument would run part of the message.
const GuardReason = "Refused: nothing in this command ran, including any heredoc or file write in it. " +
	"A backtick or $( inside double quotes is shell substitution. Resend the body in " +
	"single quotes, or written to a file first in a separate step and passed with " +
	"--file PATH (hl say, hl task new, hl task handoff, hl task memo add), or on " +
	"stdin with - through a quoted heredoc (<<'EOF')."

func startsWith(r []rune, i int, s string) bool {
	rs := []rune(s)
	return i >= 0 && i+len(rs) <= len(r) && string(r[i:i+len(rs)]) == s
}

// expands is whether text, read as the inside of double quotes, holds a substitution.
func expands(text string) bool {
	r := []rune(text)
	for i := 0; i < len(r); {
		c := r[i]
		if c == '\\' {
			i += 2
			continue
		}
		if c == '`' || startsWith(r, i, "$(") {
			return true
		}
		i++
	}
	return false
}

// heredocBody is (body, index of the newline ending it) for a heredoc whose body starts at start.
func heredocBody(command []rune, start int, dash bool, word string) (string, int) {
	var lines []string
	pos := start
	for pos < len(command) {
		stop := pos
		for stop < len(command) && command[stop] != '\n' {
			stop++
		}
		line := string(command[pos:stop])
		cmp := line
		if dash {
			cmp = strings.TrimLeft(line, "\t")
		}
		if cmp == word {
			return strings.Join(lines, "\n"), stop
		}
		lines = append(lines, line)
		pos = stop + 1
	}
	return strings.Join(lines, "\n"), len(command)
}

type pendingHeredoc struct {
	dash, quoted bool
	word         string
}

// matchHeredoc is the heredoc opener at runes[i:], and the index just past it; ok is false when there is none.
func matchHeredoc(r []rune, i int) (h pendingHeredoc, end int, ok bool) {
	m := heredocRE.FindStringSubmatch(string(r[i:]))
	if m == nil {
		return h, 0, false
	}
	n := len([]rune(m[0]))
	if m[2] != "" {
		if i+n >= len(r) || string(r[i+n]) != m[2] {
			return h, 0, false
		}
		n++
	}
	return pendingHeredoc{m[1] != "", m[2] != "", m[3]}, i + n, true
}

// Substitutes is whether the shell would substitute inside a double-quoted string or an unquoted heredoc body of this command.
func Substitutes(command string) bool {
	r := []rune(command)
	var pending []pendingHeredoc
	var quote rune
	for i := 0; i < len(r); i++ {
		c := r[i]
		switch {
		case quote == '\'':
			if c == '\'' {
				quote = 0
			}
		case quote == '"':
			if c == '\\' {
				i++
			} else if c == '"' {
				quote = 0
			} else if c == '`' || startsWith(r, i, "$(") {
				return true
			}
		case c == '\\':
			i++
		case c == '\'' || c == '"':
			quote = c
		case c == '\n' && len(pending) > 0:
			for _, p := range pending {
				var body string
				body, i = heredocBody(r, i+1, p.dash, p.word)
				if !p.quoted && expands(body) {
					return true
				}
			}
			pending = nil
		case startsWith(r, i, "<<") && !startsWith(r, i, "<<<"):
			if h, end, ok := matchHeredoc(r, i); ok {
				pending = append(pending, h)
				i = end - 1
			}
		}
	}
	return false
}

// Guard is the reason to refuse this tool call, or "" to let it through.
func Guard(tool string, toolInput any) string {
	in, _ := toolInput.(map[string]any)
	command, ok := in["command"].(string)
	if tool != "Bash" || !ok || !ocpRE.MatchString(command) {
		return ""
	}
	if Substitutes(command) {
		return GuardReason
	}
	return ""
}
