package session

import (
	"strings"
	"testing"
)

func TestHeavyGuardRefusesEachFormOutsideASlot(t *testing.T) {
	for name, c := range map[string]string{
		"vitest":             "vitest run src/a.test.ts",
		"pnpm exec vitest":   "cd apps/web && pnpm exec vitest run a.test.ts",
		"npx vitest":         "npx vitest run",
		"npx -y vitest":      "npx --yes vitest run",
		"with env":           "CI=1 vitest run",
		"go test":            "go test -race ./cmd/hl",
		"go test in a pipe":  "cd go && go test ./... 2>&1 | tail -20",
		"xcodebuild":         "xcodebuild -scheme App build",
		"simctl boot":        "xcrun simctl boot iPhone",
		"pnpm perf":          "pnpm --filter @handline/web perf",
		"perf script":        "node scripts/perf/run.ts",
		"headless Chrome":    "'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --headless=new --screenshot=x.png http://localhost",
		"after an allowed":   "tools/check.sh && go test ./...",
		"pnpm vitest":        "cd apps/web && pnpm vitest run a.test.ts",
		"pnpm filter vitest": "pnpm --filter @handline/web vitest run",
		"pnpm dlx vitest":    "pnpm dlx vitest run",
		"time go test":       "time go test ./...",
		"timeout go test":    "timeout 600 go test ./x",
		"timeout -k":         "timeout -k 5 600 go test ./x",
		"bash -c":            `bash -c "go test ./x"`,
		"sh -c":              `sh -c 'cd go; go test ./x'`,
		"in a subshell":      "(cd go && go test ./x)",
		"in a substitution":  "echo $(go test ./x)",
	} {
		why := Guard("Bash", bash(c))
		if !strings.HasPrefix(why, "Refused: ") || !strings.Contains(why, "tools/heavy-slot.sh") {
			t.Errorf("%s: %q gave %q", name, c, why)
		}
	}
}

func TestHeavyGuardLetsTheWrappersAndOtherCommandsThrough(t *testing.T) {
	for name, c := range map[string]string{
		"heavy-slot":          "tools/heavy-slot.sh vitest run a.test.ts",
		"heavy-slot go":       "./tools/heavy-slot.sh go test ./cmd/hl",
		"check.sh":            "tools/check.sh",
		"test-go.sh":          "tools/test-go.sh ./cmd/hl",
		"pnpm test":           "pnpm --filter @handline/web test",
		"root pnpm test":      "pnpm test",
		"env then wrapper":    "HANDLINE_X=1 tools/heavy-slot.sh pnpm perf",
		"grep for vitest":     "grep -rn vitest apps/web/package.json",
		"commit message":      `git commit -m "run vitest and go test"`,
		"go build":            "go build ./... && go vet ./...",
		"simctl shutdown":     "xcrun simctl shutdown all",
		"chrome no headless":  "open -a 'Google Chrome'",
		"report with go test": `hl task done T1 "go vet ok; go test -race for cmd/hl ok"`,
		"commit heredoc":      "git commit -m \"$(cat <<'EOF'\nFix it\n\ngo test now needs the slot\nEOF\n)\"",
		"slot bash -c":        `tools/heavy-slot.sh bash -c "cd go && go test ./x"`,
		"slot sh -c":          `tools/heavy-slot.sh sh -c 'cd go; go test ./x'`,
		"check then wrapper":  "tools/check.sh --fast && echo done",
		"heredoc body":        "cat <<EOF\ngo test ./x\nEOF",
		"nested bash -c":      `bash -c "bash -c 'go test ./x'"`,
		"unparsable":          "go test ./x 'unterminated",
		"parse error":         "go test ./x; echo 'x",
		"pnpm add vitest":     "pnpm add -D vitest",
		"pnpm why vitest":     "pnpm why vitest",
		"empty":               "",
	} {
		if why := Guard("Bash", bash(c)); why != "" {
			t.Errorf("%s: %q refused: %q", name, c, why)
		}
	}
	if why := Guard("Write", bash("go test ./...")); why != "" {
		t.Errorf("a Write was refused: %q", why)
	}
}

func TestHeavyGuardThatFailsLetsTheCommandRun(t *testing.T) {
	old := heavyGuard
	defer func() { heavyGuard = old }()
	heavyGuard = func(string, any) string { panic("broken") }
	if why := Guard("Bash", bash("go test ./...")); why != "" {
		t.Errorf("a guard that panicked refused: %q", why)
	}
	if why := Guard("Bash", bash("hl say worker \"`whoami`\"")); why != GuardReason {
		t.Errorf("the ocp guard did not run after it: %q", why)
	}
}
