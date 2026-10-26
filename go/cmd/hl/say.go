package main

import (
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/hlclient"
)

// sayUsage is what `hl say --help` and a say with no target print: the heredoc is the form for a body of more than a sentence.
const sayUsage = `usage: hl say <target> '<one sentence>'
       hl say <target> --file - <<'EOF'
       <the body, in Markdown: as many lines as it needs>
       EOF
options: --parent <id> (a reply, in its thread), --task T<n>, --no-task '<why>', --file PATH, --attach IMAGE (repeatable)`

// oneLineMax is the longest body hl say takes on one line without a hint to break it into lines.
const oneLineMax = 300

// say sends a message that needs no answer and starts no work (cli/say.py cmd_say), through the say method.
func say(c *hlclient.Client, args []string) (int, bool) {
	if slices.Contains(args, "-h") || slices.Contains(args, "--help") {
		fmt.Println(sayUsage)
		return 0, true
	}
	p, ok := parse(args, []string{"--file", "--parent", "--task", "--no-task", "--attach"}, nil, nil)
	if !ok {
		return 0, false
	}
	if len(p.pos) > 2 {
		return wrongArgs("too many words: the target, then the whole text in one pair of quotes")
	}
	parent, hasParent := p.opt("--parent")
	var parentN any
	ok1 := true
	if hasParent {
		parentN, ok1 = requestArg(parent)
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
		fmt.Println("say needs a target and a body\n" + sayUsage)
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
	attach, abs := p.each["--attach"], []string(nil)
	for _, a := range attach {
		f, _ := filepath.Abs(a) // the daemon reads it from its own cwd
		abs = append(abs, f)
	}
	if !strings.Contains(strings.TrimSpace(body), "\n") && utf8.RuneCountInString(body) > oneLineMax {
		fmt.Printf("hint: this body is %d characters on one line. The receiver sees its lines: next time put each point on "+
			"its own line, with `hl say %s --file - <<'EOF'`.\n", utf8.RuneCountInString(body), p.pos[0])
	}
	if !asking(c, "say") {
		return 1, true
	}
	if p.pos[0] == "human" && wantsAnswer(body) {
		fmt.Println("refused: this say to human reads as a question, and a say is read once and gone. What needs the founder's " +
			"answer is a task assigned to them: hl task new --assign human -t <time> '<title>' '<body>'. " +
			"If it is only news, say it without asking for a reply.")
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
	if len(abs) > 0 {
		params["files"] = abs
	}
	return said(c.Write("say", params)), true
}

// answerWords are the words that ask for a reply, wherever they stand in a body.
var answerWords = []string{"답장", "답해", "답변", "reply", "respond", "confirm"}

// wantsAnswer is whether a body reads as a request for the reader's answer: a line ending in a question mark, or a word that asks for a reply.
func wantsAnswer(body string) bool {
	low := strings.ToLower(body)
	for _, w := range answerWords {
		if strings.Contains(low, w) {
			return true
		}
	}
	for _, ln := range strings.Split(body, "\n") {
		if t := strings.TrimSpace(ln); strings.HasSuffix(t, "?") || strings.HasSuffix(t, "？") {
			return true
		}
	}
	return false
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
