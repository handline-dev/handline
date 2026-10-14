package reads

import (
	"slices"

	"github.com/handline/handline/go/internal/team"
)

// recentChats is how many agents TaskDetail.people.recent names.
const recentChats = 3

// askable is TaskDetail.people: whom the founder can ask about a task held by holder, each agent once. Who the founder last chatted with
// comes first, then the rest of the holder's team in team.yaml order, then everyone else. A team.yaml that cannot be read is no team.
func (e *Env) askable(holder string) (map[string]any, error) {
	agents := e.listAgents()
	rows, err := query(e.DB, "SELECT requester, target FROM requests WHERE requester = ? OR target = ? ORDER BY id DESC LIMIT 200", human, human)
	if err != nil {
		return nil, err
	}
	recent := []string{}
	for _, r := range rows {
		other := r.str("target")
		if other == human {
			other = r.str("requester")
		}
		if slices.Contains(agents, other) && !slices.Contains(recent, other) {
			if recent = append(recent, other); len(recent) == recentChats {
				break
			}
		}
	}
	mine := []string{}
	if t, err := team.LoadTeam(e.Home); err == nil && holder != "" {
		if group := t.TeamOf(holder); group != "" {
			for _, a := range t.Members(group) {
				if slices.Contains(agents, a) && !slices.Contains(recent, a) {
					mine = append(mine, a)
				}
			}
		}
	}
	others := []string{}
	for _, a := range agents {
		if !slices.Contains(recent, a) && !slices.Contains(mine, a) {
			others = append(others, a)
		}
	}
	return map[string]any{"recent": recent, "team": mine, "others": others}, nil
}
