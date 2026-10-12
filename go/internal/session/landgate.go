package session

import (
	"errors"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
)

// A land verb the session's own PreToolUse hook vouches for. Branch code (the auditor's test run, the merger's `land.sh --check`)
// runs as a child of the same session that sends `land_pass` and `land`, so the daemon cannot tell the two apart by who is calling.
// What a branch's code cannot do is be the command the hook sees: the hook runs outside the Bash sandbox, so it alone can read the
// per-session key `hl launch` keeps in a folder the sandbox hides. When the tool call is exactly `hl land --pass <sha>` or
// `hl land <sha> <message> --tree <tree>` (optionally with `--fix <dir>` and `--squash`) and nothing else, it hands the daemon the key with the verb and the sha, and the daemon
// accepts that one request from that session for a minute (landauth).

const operators = ";&|<>()"

var errShlex = errors.New("shlex")

// shlexSplit is Python's shlex.shlex(cmd, posix=True, punctuation_chars=True) with whitespace_split and no comment characters:
// words end at whitespace; a run of ;&|<>() is a word of its own; quotes and backslashes are the POSIX shell's. A quote left open
// or a backslash with nothing after it is an error.
func shlexSplit(cmd string) ([]string, error) {
	r := []rune(cmd)
	var words []string
	var tok []rune
	inWord, quoted := false, false
	flush := func() {
		if inWord && (len(tok) > 0 || quoted) {
			words = append(words, string(tok))
		}
		tok, inWord, quoted = nil, false, false
	}
	isSpace := func(c rune) bool { return c == ' ' || c == '\t' || c == '\r' || c == '\n' }
	for i := 0; i < len(r); i++ {
		c := r[i]
		switch {
		case isSpace(c):
			flush()
		case strings.ContainsRune(operators, c):
			flush()
			j := i
			for j < len(r) && strings.ContainsRune(operators, r[j]) {
				j++
			}
			words = append(words, string(r[i:j]))
			i = j - 1
		case c == '\'' || c == '"':
			inWord, quoted = true, true
			j := i + 1
			for ; j < len(r) && r[j] != c; j++ {
				if c == '"' && r[j] == '\\' {
					if j+1 >= len(r) {
						return nil, errShlex
					}
					if n := r[j+1]; n != '"' && n != '\\' {
						tok = append(tok, '\\')
					}
					j++
				}
				tok = append(tok, r[j])
			}
			if j >= len(r) {
				return nil, errShlex
			}
			i = j
		case c == '\\':
			if i+1 >= len(r) {
				return nil, errShlex
			}
			inWord = true
			i++
			tok = append(tok, r[i])
		default:
			inWord = true
			tok = append(tok, c)
		}
	}
	flush()
	return words, nil
}

func base(path string) string {
	if i := strings.LastIndex(path, "/"); i >= 0 {
		return path[i+1:]
	}
	return path
}

func onlyOperators(w string) bool {
	for _, c := range w {
		if !strings.ContainsRune(operators, c) {
			return false
		}
	}
	return true
}

// ParseLand is (verb, sha) when this Bash call is nothing but one `hl land` that sends a verb; ok is false otherwise. A command
// with any operator, substitution or second command in it vouches for nothing.
func ParseLand(toolName string, toolInput any) (verb, sha string, ok bool) {
	in, isMap := toolInput.(map[string]any)
	if toolName != "Bash" || !isMap {
		return
	}
	cmd, isStr := in["command"].(string)
	if !isStr || strings.ContainsAny(cmd, "`$\n\r") {
		return
	}
	words, err := shlexSplit(cmd)
	if err != nil || len(words) < 3 || words[1] != "land" {
		return
	}
	known := false
	for _, n := range paths.Commands {
		known = known || base(words[0]) == n
	}
	if !known {
		return
	}
	for _, w := range words {
		if onlyOperators(w) || strings.HasPrefix(w, "#") {
			return
		}
	}
	rest := words[2:]
	var positional []string
	tree, housekeeping := false, false
	for i := 0; i < len(rest); {
		w := rest[i]
		switch {
		case w == "--tree":
			tree = true
			i += 2
		case w == "--fix": // the merger's housekeeping directory: a land's, never a pass's
			housekeeping = true
			i += 2
		case w == "--squash":
			housekeeping = true
			i++
		case strings.HasPrefix(w, "-") && w != "--pass":
			return // --check, --list and anything unknown send no verb
		default:
			if w != "--pass" {
				positional = append(positional, w)
			}
			i++
		}
	}
	// Whether this is a pass is read off the whole list, as the spec does: a --pass in the place of --tree's value is one too.
	if slices.Contains(rest, "--pass") {
		if len(positional) == 1 && !housekeeping {
			return "land_pass", positional[0], true
		}
		return
	}
	if tree && len(positional) == 2 {
		return "land", positional[0], true
	}
	return
}

// sendsLand finds an `hl land` anywhere in a Bash call (landgate.py _SENDS).
var sendsLand = regexp.MustCompile(`(?:^|[` + space + `;&|(])(?:[^` + space + `]*/)?(?:` + strings.Join(paths.Commands, "|") +
	`)[` + space + `]+land[` + space + `]`)

// MissesReason is why PreToolUse refuses a land command nothing would vouch for (landgate.py MISSES).
const MissesReason = "Refused before it ran: `hl land` sends its verb only when it is the whole Bash call, so this one would be turned " +
	"away. Type it alone on one line: no cd or && before it, no 2>&1, pipe, ; or # after it, and no $ or backtick " +
	"anywhere, quoted or not. `hl land --pass <sha>`, or `hl land <sha> '<message>' --tree <tree>`."

// Misses is MissesReason when this Bash call holds an `hl land` that would send a verb (a --pass or a --tree, no --check or
// --list) with more around it than ParseLand accepts, and the session has a key to vouch with; "" otherwise.
func Misses(keyHeld func(string) string, agent, toolName string, toolInput any) string {
	in, isMap := toolInput.(map[string]any)
	if toolName != "Bash" || !isMap {
		return ""
	}
	cmd, _ := in["command"].(string)
	if !sendsLand.MatchString(cmd) || !(strings.Contains(cmd, "--pass") || strings.Contains(cmd, "--tree")) ||
		strings.Contains(cmd, "--check") || strings.Contains(cmd, "--list") {
		return ""
	}
	if _, _, ok := ParseLand(toolName, toolInput); ok || keyHeld(agent) == "" {
		return ""
	}
	return MissesReason
}

// LandVoucher is what Authorize needs of the rest of the system: the session key `hl launch` kept, and the daemon.
type LandVoucher struct {
	KeyHeld func(agent string) string // "" when none is kept
	Send    func(req map[string]any, timeout time.Duration) map[string]any
}

// Authorize vouches for this tool call to the daemon if it is a land command. True when the daemon took it.
func (v LandVoucher) Authorize(agent, toolName string, toolInput any) bool {
	verb, sha, ok := ParseLand(toolName, toolInput)
	if !ok {
		return false
	}
	key := v.KeyHeld(agent)
	if key == "" {
		return false
	}
	reply := v.Send(map[string]any{"verb": "land_authorize", "agent": agent, "land_verb": verb, "sha": sha, "key": key}, 5*time.Second)
	got, _ := reply["ok"].(bool)
	return got
}
