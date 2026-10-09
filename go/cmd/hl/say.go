package main

import (
	"fmt"
	"slices"
	"strings"
	"unicode"

	"github.com/handline/handline/go/internal/hlclient"
)

// say sends a message that needs no answer and starts no work (cli/say.py cmd_say), through the say method.
func say(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--file", "--parent", "--task", "--no-task"}, nil, nil)
	if !ok || len(p.pos) > 2 {
		return 0, false
	}
	parent, hasParent := p.opt("--parent")
	parentN, ok1 := 0, true
	if hasParent {
		parentN, ok1 = integer(parent)
	}
	taskN, hasTask, ok2 := optTask(p, "--task")
	noTask, hasNoTask := p.opt("--no-task")
	// A blank the say method refuses as a parameter is what the legacy verb Python sends would read: Python says what it does.
	blank := func(s string) bool { return strings.TrimSpace(s) == "" }
	if !ok1 || !ok2 || (hasNoTask && blank(noTask)) || slices.ContainsFunc(p.pos, blank) {
		return 0, false
	}
	c.NoteVerbChain([]string{"say", ""})
	if len(p.pos) == 0 {
		fmt.Println("say needs a target and a body")
		return 1, true
	}
	var inline *string
	if len(p.pos) == 2 {
		inline = &p.pos[1]
	}
	file, _ := p.opt("--file")
	body, why := textArg(inline, file, "body")
	if why != "" {
		fmt.Println(why)
		return 1, true
	}
	if file == "" && (inline == nil || *inline != "-") && lostToQuoting(body) {
		fmt.Println("warning: the body has a gap that may be text lost to shell quoting (backticks or $( ) " +
			"inside double quotes run as commands). Resend with single quotes, or `--file -` and a " +
			"heredoc, if text is missing.")
	}
	if !asking(c, "say") {
		return 1, true
	}
	params := map[string]any{"target": p.pos[0], "body": body}
	if hasParent {
		params["parent"] = parentN
	}
	if hasTask {
		params["task"] = taskN
	}
	if hasNoTask {
		params["no_task"] = noTask
	}
	return said(c.Write("say", params)), true
}

// lostToQuoting is say._LOST: two or more spaces between word characters, the gap a shell leaves where it ran
// `...` or $(...) inside double quotes.
func lostToQuoting(body string) bool {
	r := []rune(body)
	for i := range r {
		if !word(r[i]) {
			continue
		}
		j := i + 1
		for j < len(r) && r[j] == ' ' {
			j++
		}
		if j-i-1 >= 2 && j < len(r) && word(r[j]) {
			return true
		}
	}
	return false
}

// word is Python's \w on str: a letter, a number or an underscore.
func word(r rune) bool { return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r) }
