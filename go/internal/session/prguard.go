package session

import (
	"regexp"

	"github.com/handline/handline/go/internal/protected"
)

// PRAgent is the one agent that opens pull requests: the others ask it with `hl pr request`.
const PRAgent = "handline.pr"

// PRGuardReason is why a Bash call that opens a pull request is refused for every other agent.
const PRGuardReason = "Refused: only " + PRAgent + " opens pull requests. Push the branch, then run " +
	"`hl pr request <branch> T<n> [--base <ref>]`: it opens a task for " + PRAgent + ", which checks the branch and opens the PR."

var (
	ghPRCreateRE = regexp.MustCompile(`(?:^|[` + space + `;&|(])(?:[^` + space + `]*/)?gh[` + space + `]+pr[` + space + `]+(?:create|new)(?:[` + space + `]|$)`)
	ghAPIRE      = regexp.MustCompile(`(?:^|[` + space + `;&|(])(?:[^` + space + `]*/)?gh[` + space + `]+api(?:[` + space + `][^;&|\n]*|$)`)
	pullsPathRE  = regexp.MustCompile(`repos/[^` + space + `/]+/[^` + space + `/]+/pulls(?:["'?` + space + `]|$)`)
	postRE       = regexp.MustCompile(`(?i)(?:-X|--method)(?:=|[` + space + `]*)["']?POST\b`)
	getRE        = regexp.MustCompile(`(?i)(?:-X|--method)(?:=|[` + space + `]*)["']?GET\b`)
	mutationRE   = regexp.MustCompile(`\bcreatePullRequest\b`)
	fieldRE      = regexp.MustCompile(`(?:^|[` + space + `])(?:-f|-F|--field|--raw-field|--input)(?:=|[` + space + `]|$)`)
)

// PRGuard is the reason to refuse a Bash call that opens a pull request (`gh pr create`, `gh pr new`, a POST to a repo's
// pulls through `gh api`, or the createPullRequest mutation) from any agent but PRAgent, "" to let it through. gh api
// sends a POST when given a field, unless told GET.
func PRGuard(agent, tool string, toolInput any) string {
	in, _ := toolInput.(map[string]any)
	command, ok := in["command"].(string)
	if tool != "Bash" || !ok || agent == PRAgent || agent == protected.Human {
		return ""
	}
	if ghPRCreateRE.MatchString(command) {
		return PRGuardReason
	}
	for _, call := range ghAPIRE.FindAllString(command, -1) {
		if mutationRE.MatchString(call) ||
			pullsPathRE.MatchString(call) && (postRE.MatchString(call) || fieldRE.MatchString(call) && !getRE.MatchString(call)) {
			return PRGuardReason
		}
	}
	return ""
}
