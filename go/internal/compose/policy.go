package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/structured"
)

// SharedPolicy is the permission policy of every structured agent: the shell guard, and the tool-name rules of the shared settings'
// permissions, which the session itself does not load (it reads project settings and the hooks only). The file is read at each call.
//
// A rule with an argument (Bash(ls:*), Read(~/.ssh/**)) is not applied as an allow: the session runs with no sandbox, so what such a
// rule lets through is not bounded by what the settings' hide lists keep a terminal agent out of. A deny or ask rule with an argument
// takes the whole tool off the allow list, and a bare deny refuses the tool. Settings that cannot be read allow nothing.
func SharedPolicy(home string) func(agent string) structured.Policy {
	return func(string) structured.Policy {
		p := structured.Policy{Guard: session.Guard}
		b, err := os.ReadFile(filepath.Join(home, "shared", ".claude", "settings.json"))
		if err != nil {
			return p
		}
		var s struct {
			Permissions struct {
				Allow []string `json:"allow"`
				Deny  []string `json:"deny"`
				Ask   []string `json:"ask"`
			} `json:"permissions"`
		}
		if json.Unmarshal(b, &s) != nil {
			return p
		}
		held := map[string]bool{} // tools a deny or ask rule names, with or without an argument
		for _, r := range s.Permissions.Deny {
			tool, bare := splitRule(r)
			held[tool] = true
			if bare {
				p.Deny = append(p.Deny, tool)
			}
		}
		for _, r := range s.Permissions.Ask {
			held[strings.SplitN(r, "(", 2)[0]] = true
		}
		for _, r := range s.Permissions.Allow {
			if tool, bare := splitRule(r); bare && !held[tool] {
				p.Allow = append(p.Allow, tool)
			}
		}
		return p
	}
}

// splitRule is a settings.json permission rule's tool, and whether it names the tool alone: "Bash(ls:*)" is Bash with an argument, "Read" is bare.
func splitRule(rule string) (tool string, bare bool) {
	tool, _, hasArg := strings.Cut(rule, "(")
	return tool, !hasArg
}
