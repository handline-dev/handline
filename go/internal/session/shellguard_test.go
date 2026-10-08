package session

import (
	"strings"
	"testing"
)

func bash(c string) map[string]any { return map[string]any{"command": c} }

func TestGuardLetsASafeCommandThrough(t *testing.T) {
	for _, c := range []string{`hl say worker "just a note"`, "echo \"`whoami`\"", `hl say worker 'a $(b)'`} {
		if g := Guard("Bash", bash(c)); g != "" {
			t.Errorf("%q refused: %s", c, g)
		}
	}
	if g := Guard("Bash", bash("hl\u00a0say \"$(x)\"")); g == "" {
		t.Error("a no-break space is white space to the spec's \\s")
	}
	if g := Guard("Write", bash("hl say worker \"`whoami`\"")); g != "" {
		t.Errorf("a tool other than Bash was looked at: %s", g)
	}
	if g := Guard("Bash", "not a map"); g != "" {
		t.Errorf("a non-map input was refused: %s", g)
	}
}

func TestGuardRefusesASubstitution(t *testing.T) {
	why := Guard("Bash", bash("hl say worker \"`whoami`\""))
	if !strings.HasPrefix(why, "Refused: nothing in this command ran, including any heredoc or file write in it.") {
		t.Fatalf("reason: %q", why)
	}
	if !strings.Contains(why, "hl task handoff") || !strings.Contains(why, "--file") {
		t.Errorf("reason does not name --file: %q", why)
	}
	if again := Guard("Bash", bash(`hl say worker "$(whoami)"`)); again != why {
		t.Errorf("$( refused differently: %q", again)
	}
}

func TestSubstitutes(t *testing.T) {
	cases := []struct {
		cmd  string
		want bool
	}{
		{`echo "a \$(b)"`, false},
		{`echo "a \` + "`" + `b"`, false},
		{`echo '$(b)'`, false},
		{`echo "$(b)"`, true},
		{`echo $(b)`, false}, // unquoted: the shell runs it, and the agent meant it
		{"hl say x <<EOF\nhello $(b)\nEOF\n", true},
		{"hl say x <<'EOF'\nhello $(b)\nEOF\n", false},
		{"hl say x <<\"EOF\"\nhello `b`\nEOF\n", false},
		{"hl say x <<-EOF\n\thello `b`\n\tEOF\n", true},
		{"hl say x <<-EOF\n\thello\n\tEOF\necho \"$(b)\"", true},
		{"hl say x <<EOF\nescaped \\$(b)\nEOF\n", false},
		{"cat <<<\"$(b)\"", true},
		{"hl say x <<'EOF\nhello `b`\n", false}, // an unclosed quote on the word is no heredoc
	}
	for _, c := range cases {
		if got := Substitutes(c.cmd); got != c.want {
			t.Errorf("Substitutes(%q) = %v, want %v", c.cmd, got, c.want)
		}
	}
}
