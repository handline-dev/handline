package team

import (
	_ "embed"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/handline/handline/go/internal/textsafe"
)

// DefaultRules is the house culture each team's rules file starts as; the launch prompt itself carries only hl facts.
//
//go:embed default_rules.md
var DefaultRules string

func rulesDir(home string) string { return filepath.Join(home, "shared", "team") }

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
		b, err := os.ReadFile(filepath.Join(rulesDir(home), name+".md"))
		if !slices.Contains(t.Teams[name], agent) || err != nil {
			continue
		}
		if text := textsafe.Strip(string(b)); text != "" {
			out = append(out, fmt.Sprintf("# Rules for team %s\n%s", name, text))
		}
	}
	return out
}

// SeedRules writes DefaultRules as the rules file of each team never seeded before, and names it in shared/team/.seeded, so a new
// team starts with the house rules while a file its team emptied or deleted stays that way.
func SeedRules(home string, t *Team) error {
	if t == nil {
		return nil
	}
	marker := filepath.Join(rulesDir(home), ".seeded")
	b, err := os.ReadFile(marker)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	seeded := strings.Fields(string(b))
	var fresh []string
	for name := range t.Teams {
		if !slices.Contains(seeded, name) {
			fresh = append(fresh, name)
		}
	}
	if len(fresh) == 0 {
		return nil
	}
	slices.Sort(fresh)
	if err := os.MkdirAll(rulesDir(home), 0o755); err != nil {
		return err
	}
	for _, name := range fresh {
		f, err := os.OpenFile(filepath.Join(rulesDir(home), name+".md"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if os.IsExist(err) {
			continue // a team that already wrote its own rules keeps them
		}
		if err != nil {
			return err
		}
		_, err = f.WriteString(DefaultRules)
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return err
		}
	}
	m, err := os.OpenFile(marker, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	_, err = m.WriteString(strings.Join(fresh, "\n") + "\n")
	if cerr := m.Close(); err == nil {
		err = cerr
	}
	return err
}
