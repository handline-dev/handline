package session

import (
	"encoding/json"
	"testing"
)

func TestPRGuardRefusesOpeningAPullRequestForAllButThePRAgent(t *testing.T) {
	opens := []string{
		`gh pr create -B main --title "x" --body-file b.md`,
		`cd go && gh pr create --fill`,
		`git push -u origin x && /opt/homebrew/bin/gh pr new -B main`,
		`gh api repos/handline-dev/handline/pulls -f title=x -f head=b -f base=main`,
		`gh api -X POST repos/{owner}/{repo}/pulls --input body.json`,
		`gh api --method=post "repos/o/r/pulls" -F draft=true`,
		`gh api graphql -f query='mutation { createPullRequest(input: {}) { pullRequest { url } } }'`,
	}
	for _, c := range opens {
		if why := PRGuard("a.one", "Bash", bash(c)); why != PRGuardReason {
			t.Errorf("%q let through for a builder: %q", c, why)
		}
		if why := PRGuard(PRAgent, "Bash", bash(c)); why != "" {
			t.Errorf("%q refused for %s: %q", c, PRAgent, why)
		}
	}
	reads := []string{
		`gh pr view 257 --json state`,
		`gh pr list --state open`,
		`gh pr checks 257`,
		`gh api repos/o/r/pulls`,
		`gh api repos/o/r/pulls -X GET -f state=open`,
		`gh api repos/o/r/pulls/257/comments -f body=x`,
		`gh api repos/o/r/pulls/257 --jq .mergeable`,
		`echo created; gh pr view 1`,
	}
	for _, c := range reads {
		if why := PRGuard("a.one", "Bash", bash(c)); why != "" {
			t.Errorf("%q refused: %q", c, why)
		}
	}
	if why := PRGuard("a.one", "Write", map[string]any{"command": "gh pr create"}); why != "" {
		t.Errorf("a tool other than Bash was looked at: %q", why)
	}
}

func TestPreToolUseRunsThePRGuard(t *testing.T) {
	root := t.TempDir()
	t.Setenv("HANDLINE_TEST_ROOT", root)
	input, _ := json.Marshal(bash("gh pr create -B main"))
	if got := decide(t, root, guardCase{Name: "builder", Tool: "Bash", Input: input}); got != PRGuardReason {
		t.Errorf("builder: %q", got)
	}
	if got := decide(t, root, guardCase{Name: "pr", Agent: PRAgent, Tool: "Bash", Input: input}); got != "" {
		t.Errorf("%s: %q", PRAgent, got)
	}
}
