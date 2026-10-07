package term

import (
	"strings"
)

// What an open prompt says about itself, read from its screen text and nothing else (handline/session/promptparse.py): the tool,
// command and reason of a permission prompt, and which dialog an unnumbered-title dialog is. A field the screen does not state is
// absent, never a guess; a prompt this cannot read is still listed, with its raw lines.
//
// Measured on the captures in contract/prompts/: a permission prompt's title names its tool ("Bash command"), the command
// follows, boxed with "│" or bare, then a description; a boxed run after the description is the reason the call is not allowed
// outright. Only a title seen in a capture is read at all.
var promptTools = map[string]string{"Bash command": "Bash"}

const box = "│"

var exitOptions = []string{"Exit and stop tasks", "Exit now"}

func unbox(line string) string {
	return strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(line), box))
}

// Fields are the tool, command and reason of a permission prompt; each is "" when the screen does not state it.
type Fields struct{ Tool, Command, Reason string }

// PermissionFields reads a permission prompt with this title and the lines between it and its question.
func PermissionFields(title string, lines []string) Fields {
	tool, ok := promptTools[strings.TrimSpace(title)]
	if !ok {
		return Fields{} // a title never captured: nothing is guessed
	}
	var ls []string
	for _, ln := range lines {
		if strings.TrimSpace(ln) != "" {
			ls = append(ls, ln)
		}
	}
	boxed := make([]bool, len(ls))
	for i, ln := range ls {
		boxed[i] = strings.HasPrefix(strings.TrimSpace(ln), box)
	}
	var command, reason string
	switch {
	case len(ls) > 0 && boxed[0]:
		n := 0
		for n < len(ls) && boxed[n] {
			n++
		}
		var parts []string
		for _, ln := range ls[:n] {
			parts = append(parts, unbox(ln))
		}
		command = strings.Join(parts, "\n")
		rest := boxed[n:]
		if len(rest) >= 2 && !rest[0] && rest[len(rest)-1] {
			m := len(ls) - 1
			for m > n && boxed[m-1] {
				m--
			}
			parts = parts[:0]
			for _, ln := range ls[m:] {
				parts = append(parts, unbox(ln))
			}
			reason = strings.Join(parts, "\n")
		}
	case len(ls) > 0:
		command = strings.TrimSpace(ls[0])
	}
	return Fields{tool, command, reason}
}

// DialogKind is `exit_confirm` for the exit confirmation (an option that exits, measured as "Exit and stop tasks" and "Exit
// now"), else `dialog`.
func DialogKind(labels []string) string {
	for _, l := range labels {
		l = strings.TrimSpace(l)
		for _, e := range exitOptions {
			if l == e {
				return "exit_confirm"
			}
		}
	}
	return "dialog"
}
