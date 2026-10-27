package session

import (
	"path"
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

// heavyWrapper is the wrapper a refused command is told to use.
const heavyWrapper = "tools/heavy-slot.sh"

// transparent are the words that run the command after them as it is.
var transparent = map[string]bool{"exec": true, "time": true, "nice": true, "env": true, "command": true, "sudo": true, "caffeinate": true,
	"timeout": true}

var shells = map[string]bool{"bash": true, "sh": true, "zsh": true}

// heavyKind is what a command is that must run in a slot, "" when it is not one. words is one command, its leading assignments and
// transparent words already dropped.
func heavyKind(words []string) string {
	if len(words) == 0 {
		return ""
	}
	name := path.Base(words[0])
	rest := words[1:]
	has := func(w string) bool {
		for _, r := range rest {
			if r == w {
				return true
			}
		}
		return false
	}
	switch name {
	case "vitest":
		return "vitest"
	case "npx", "pnpx", "bunx":
		for _, r := range rest {
			if strings.HasPrefix(r, "-") {
				continue
			}
			if path.Base(r) == "vitest" {
				return "vitest"
			}
			break
		}
	case "pnpm", "npm", "yarn":
		if packageVitest(rest) {
			return "vitest"
		}
		if has("perf") {
			return "perf"
		}
	case "go":
		if len(rest) > 0 && rest[0] == "test" {
			return "go test"
		}
	case "xcodebuild":
		return "xcodebuild"
	case "xcrun":
		if len(rest) >= 2 && rest[0] == "simctl" && rest[1] == "boot" {
			return "simctl boot"
		}
	case "node":
		for _, r := range rest {
			if strings.HasSuffix(r, "scripts/perf/run.ts") {
				return "perf"
			}
		}
	}
	return ""
}

// packageVitest is whether the arguments of pnpm, npm or yarn run vitest: it follows the flags and any exec, dlx or run (pnpm and yarn
// run a bin without exec), so `pnpm add vitest` and `pnpm why vitest` are not it.
func packageVitest(args []string) bool {
	for i := 0; i < len(args); i++ {
		switch a := args[i]; {
		case a == "--filter" || a == "-F" || a == "-C" || a == "--dir":
			i++ // these take a value
		case strings.HasPrefix(a, "-") || a == "exec" || a == "dlx" || a == "run":
		default:
			return a == "vitest"
		}
	}
	return false
}

// headlessChrome is whether a command is a Chrome run with --headless.
func headlessChrome(words []string) bool {
	if len(words) == 0 || !strings.Contains(strings.ToLower(path.Base(words[0])), "chrom") {
		return false
	}
	for _, w := range words[1:] {
		if strings.HasPrefix(w, "--headless") {
			return true
		}
	}
	return false
}

// literal is a word's text when it is fixed before the shell runs (quotes removed), "" when it holds an expansion.
func literal(w *syntax.Word) string {
	var b strings.Builder
	for _, p := range w.Parts {
		switch p := p.(type) {
		case *syntax.Lit:
			b.WriteString(p.Value)
		case *syntax.SglQuoted:
			b.WriteString(p.Value)
		case *syntax.DblQuoted:
			for _, q := range p.Parts {
				l, ok := q.(*syntax.Lit)
				if !ok {
					return ""
				}
				b.WriteString(l.Value)
			}
		default:
			return ""
		}
	}
	return b.String()
}

// heavyIn is the first heavy command in a shell script, "" when there is none or it cannot be parsed. depth is how many shell -c strings
// it is inside: one level is read.
func heavyIn(script string, depth int) (kind string, words []string) {
	file, err := syntax.NewParser(syntax.Variant(syntax.LangBash)).Parse(strings.NewReader(script), "")
	if err != nil {
		return "", nil
	}
	syntax.Walk(file, func(n syntax.Node) bool {
		call, ok := n.(*syntax.CallExpr)
		if kind != "" || !ok {
			return kind == ""
		}
		var w []string
		for _, a := range call.Args {
			w = append(w, literal(a))
		}
		for len(w) > 0 && (transparent[w[0]] || strings.HasPrefix(w[0], "-")) {
			if w[0] == "timeout" {
				w = w[1:]
				for len(w) > 0 && strings.HasPrefix(w[0], "-") {
					if (w[0] == "-k" || w[0] == "-s") && len(w) > 1 { // these take a value
						w = w[1:]
					}
					w = w[1:]
				}
				if len(w) > 0 {
					w = w[1:] // the duration
				}
				continue
			}
			w = w[1:]
		}
		if len(w) == 0 {
			return true
		}
		if k := heavyKind(w); k != "" {
			kind, words = k, w
		} else if headlessChrome(w) {
			kind, words = "headless Chrome", w
		} else if shells[path.Base(w[0])] && depth == 0 {
			for i, a := range w[1:] {
				if a == "-c" && i+2 < len(w) {
					kind, words = heavyIn(w[i+2], depth+1)
					break
				}
			}
		}
		return kind == ""
	})
	return kind, words
}

// HeavyGuard is the reason to refuse a Bash call that runs a heavy test or build outside a heavy slot, "" to let it through. It reads the
// command as shell (mvdan.cc/sh), so words inside quotes and heredoc bodies are not commands. A command whose first word is
// tools/heavy-slot.sh, tools/check.sh or tools/test-go.sh is in a slot, with all it runs, and so is `pnpm test`, whose scripts take one.
// bash -c and sh -c are read one level deep; xargs and scripts are not read. A command that does not parse is let through.
func HeavyGuard(tool string, toolInput any) string {
	in, _ := toolInput.(map[string]any)
	command, ok := in["command"].(string)
	if tool != "Bash" || !ok {
		return ""
	}
	kind, words := heavyIn(command, 0)
	if kind == "" {
		return ""
	}
	return "Refused: " + kind + " runs outside a heavy slot, and the machine swaps when several do. Nothing in this command ran. " +
		"Run it through " + heavyWrapper + " (" + heavyWrapper + " " + strings.Join(words, " ") + "), or use tools/check.sh, " +
		"tools/test-go.sh or `pnpm test`, which take a slot themselves."
}

var heavyGuard = HeavyGuard

// safely is guard's answer, "" when it fails: a guard that breaks lets the command run.
func safely(guard func(string, any) string, tool string, toolInput any) (why string) {
	defer func() {
		if recover() != nil {
			why = ""
		}
	}()
	return guard(tool, toolInput)
}
