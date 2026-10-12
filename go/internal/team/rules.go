package team

import (
	"fmt"
	"os"
	"path/filepath"
	"slices"

	"github.com/handline/handline/go/internal/textsafe"
)

// Rules is the shared/team/<team>.md of each team the agent is in, as "# Rules for team X\n<text>" (launch._team_rules).
func Rules(home, agent string, t *Team) []string {
	if t == nil {
		return nil
	}
	names := make([]string, 0, len(t.Teams))
	for name := range t.Teams {
		names = append(names, name)
	}
	slices.Sort(names)
	var out []string
	for _, name := range names {
		b, err := os.ReadFile(filepath.Join(home, "shared", "team", name+".md"))
		if !slices.Contains(t.Teams[name], agent) || err != nil {
			continue
		}
		if text := textsafe.Strip(string(b)); text != "" {
			out = append(out, fmt.Sprintf("# Rules for team %s\n%s", name, text))
		}
	}
	return out
}
