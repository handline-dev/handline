package claude

import "testing"

func TestAllowRuleIsThePlainCommandsPrefixAndNothingForWhatRunsOthers(t *testing.T) {
	for _, c := range []struct{ tool, command, want string }{
		{"Bash", "gh pr create --title x --body y", "Bash(gh pr create:*)"},
		{"Bash", "gh pr create", "Bash(gh pr create:*)"},
		{"Bash", "gh pr create --draft", "Bash(gh pr create:*)"},
		{"Bash", "make", "Bash(make)"},
		{"Bash", "rm -rf build", "Bash(rm -rf build)"},
		{"Bash", "bash -c 'x'", ""},
		{"Bash", "gh pr create && rm -rf /", ""},
		{"Bash", "echo $(id)", ""},
		{"Write", "gh pr create", ""},
		{"Bash", "", ""},
	} {
		if got := AllowRule(c.tool, c.command); got != c.want {
			t.Errorf("%s %q: %q, want %q", c.tool, c.command, got, c.want)
		}
	}
}
