package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/timefmt"
)

// parsed is a command line read against one argparse parser: the positionals in order, each option's value under its
// long name, and each flag given.
type parsed struct {
	pos   []string
	opts  map[string]string
	flags map[string]bool
}

func (p parsed) opt(name string) (string, bool) { v, ok := p.opts[name]; return v, ok }

// parse reads args with these options: valued names take the next argument or `--name=value`, flags take none, and
// alias maps a short name to its long one. ok is false for anything argparse would read some other way or refuse: an
// unknown or abbreviated option, `--`, `-h`, a valued option with nothing after it. Python then parses it and says so.
func parse(args []string, valued, flags []string, alias map[string]string) (parsed, bool) {
	p := parsed{opts: map[string]string{}, flags: map[string]bool{}}
	isValued := map[string]bool{}
	for _, v := range valued {
		isValued[v] = true
	}
	isFlag := map[string]bool{}
	for _, f := range flags {
		isFlag[f] = true
	}
	for i := 0; i < len(args); i++ {
		a := args[i]
		if !looksLikeOption(a) {
			p.pos = append(p.pos, a)
			continue
		}
		name, value, inline := strings.Cut(a, "=")
		if long, ok := alias[name]; ok {
			name = long
		}
		switch {
		case isFlag[name] && !inline:
			p.flags[name] = true
		case isValued[name] && inline:
			p.opts[name] = value
		case isValued[name] && i+1 < len(args) && !looksLikeOption(args[i+1]):
			i++
			p.opts[name] = args[i]
		default:
			return p, false
		}
	}
	return p, true
}

// looksLikeOption is what argparse takes for an option: a dash and more, `--` included. "-" alone is a value (stdin).
func looksLikeOption(a string) bool { return len(a) > 1 && a[0] == '-' }

// The argparse `type`s of cli/task.py, from actions: each is ok false where argparse would refuse.

func taskRef(text string) (int, bool) {
	t := strings.TrimSpace(text)
	if len(t) < 2 || (t[0] != 'T' && t[0] != 't') || !digits(t[1:]) {
		return 0, false
	}
	n, err := strconv.Atoi(t[1:])
	return n, err == nil
}

func taskRefs(texts []string) ([]int, bool) {
	out := make([]int, 0, len(texts))
	for _, t := range texts {
		n, ok := taskRef(t)
		if !ok {
			return nil, false
		}
		out = append(out, n)
	}
	return out, true
}

func priority(text string) (int, bool) {
	t := strings.ToUpper(strings.TrimSpace(text))
	t = strings.TrimPrefix(t, "P")
	if t == "1" || t == "2" || t == "3" {
		return int(t[0] - '0'), true
	}
	return 0, false
}

func expects(text string) (string, bool) {
	t := strings.ToLower(strings.TrimSpace(text))
	switch t {
	case "sha", "verdict", "decision", "report":
		return t, true
	}
	return "", false
}

// maxTaskTimeout is actions.MAX_TASK_TIMEOUT_S.
const maxTaskTimeout = 4 * 3600

func taskTimeout(text string) (int64, bool) {
	secs, err := timefmt.ParseSpan(text)
	return secs, err == nil && secs <= maxTaskTimeout
}

// integer is argparse's type=int for the plain forms; anything fancier is Python's.
func integer(text string) (int, bool) {
	if !digits(text) {
		return 0, false
	}
	n, err := strconv.Atoi(text)
	return n, err == nil
}

func digits(s string) bool { return s != "" && strings.Trim(s, "0123456789") == "" }

// textArg is cli/ask._text_arg: inline or a --file, never both; "-" as either reads stdin, and so does neither when
// stdin is not a terminal. (text, "") or ("", why not).
func textArg(inline *string, file, label string) (string, string) {
	if file != "" && inline != nil {
		return "", fmt.Sprintf("give %s inline or with --file, not both", label)
	}
	if file == "-" || (inline != nil && *inline == "-") {
		text := readStdin()
		if strings.TrimSpace(text) == "" {
			return "", fmt.Sprintf("%s is required, and stdin (-) was empty", label)
		}
		return text, ""
	}
	if file != "" {
		b, err := os.ReadFile(file)
		if err != nil {
			return "", fmt.Sprintf("could not read %s: %v", file, err)
		}
		return string(b), ""
	}
	if inline == nil {
		text := ""
		if !hlclient.StdinIsTerminal() {
			text = readStdin()
		}
		if strings.TrimSpace(text) == "" {
			return "", fmt.Sprintf("%s is required (inline, --file PATH, or on stdin)", label)
		}
		return text, ""
	}
	return *inline, ""
}

func readStdin() string {
	var b strings.Builder
	buf := make([]byte, 64<<10)
	for {
		n, err := os.Stdin.Read(buf)
		b.Write(buf[:n])
		if err != nil {
			return b.String()
		}
	}
}

// shQuote is Python's shlex.quote.
func shQuote(s string) string {
	if s == "" {
		return "''"
	}
	safe := true
	for _, r := range s {
		if !(r < 128 && (r == '_' || r == '@' || r == '%' || r == '+' || r == '=' || r == ':' || r == ',' || r == '.' ||
			r == '/' || r == '-' || ('a' <= r && r <= 'z') || ('A' <= r && r <= 'Z') || ('0' <= r && r <= '9'))) {
			safe = false
			break
		}
	}
	if safe {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
}

// pyPath is str(pathlib.Path(p)): repeated slashes and "." parts dropped, no trailing slash, "." for nothing.
func pyPath(p string) string {
	abs := strings.HasPrefix(p, "/")
	var parts []string
	for _, part := range strings.Split(p, "/") {
		if part != "" && part != "." {
			parts = append(parts, part)
		}
	}
	out := strings.Join(parts, "/")
	switch {
	case abs:
		return "/" + out
	case out == "":
		return "."
	}
	return out
}
