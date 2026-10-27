package session

import (
	"path"
	"regexp"
	"strings"
)

// heavyWrapper is the wrapper a refused command is told to use.
const heavyWrapper = "tools/heavy-slot.sh"

// segmentSplitRE cuts a command line into the commands the shell runs one after the other (or at once).
var segmentSplitRE = regexp.MustCompile(`&&|\|\||[;&|\n(){}]`)

var envAssignRE = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*=`)

// transparent are the words that run the command after them as it is.
var transparent = map[string]bool{"exec": true, "time": true, "nice": true, "env": true, "command": true, "sudo": true, "caffeinate": true,
	"timeout": true}

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
		if has("vitest") && (has("exec") || has("dlx") || name == "yarn") {
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

// headlessChrome is whether the segment starts a Chrome (a path, possibly quoted for the space in it, or chrome / chromium / google-chrome)
// with --headless.
func headlessChrome(seg string) bool {
	t := strings.TrimSpace(seg)
	i := strings.Index(t, "--headless")
	if i < 0 {
		return false
	}
	head := strings.ToLower(t[:i])
	return strings.Contains(head, "chrom") && (strings.ContainsAny(t[:1], `/'"`) || strings.HasPrefix(head, "chrom") ||
		strings.HasPrefix(head, "google-chrome"))
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

// HeavyGuard is the reason to refuse a Bash call that runs a heavy test or build outside a heavy slot, "" to let it through. Only a command
// that is itself a heavy one is refused, so tools/heavy-slot.sh, tools/check.sh, tools/test-go.sh and `pnpm test` (whose scripts take a
// slot) pass. It reads the command loosely, and a command it cannot read is let through.
func HeavyGuard(tool string, toolInput any) string {
	in, _ := toolInput.(map[string]any)
	command, ok := in["command"].(string)
	if tool != "Bash" || !ok {
		return ""
	}
	for _, seg := range segmentSplitRE.Split(command, -1) {
		words := strings.Fields(seg)
		for len(words) > 0 && (envAssignRE.MatchString(words[0]) || transparent[words[0]] || strings.HasPrefix(words[0], "-")) {
			words = words[1:]
		}
		kind := heavyKind(words)
		if kind == "" && headlessChrome(seg) {
			kind = "headless Chrome"
		}
		if kind != "" {
			return "Refused: " + kind + " runs outside a heavy slot, and the machine swaps when several do. Nothing in this command ran. " +
				"Run it through " + heavyWrapper + " (" + heavyWrapper + " " + strings.Join(words, " ") + "), or use tools/check.sh, " +
				"tools/test-go.sh or `pnpm test`, which take a slot themselves."
		}
	}
	return ""
}
