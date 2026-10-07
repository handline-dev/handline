package team

import "strings"

// SupervisorOf is actions.routing.supervisor_of: the agent this one answers to. A supervisor edge whose source names one agent
// counts, the most specific destination winning (a name, then a team, then everyone); with no edge the name says (`harbor.sea`
// answers to `harbor`) when that agent is known.
func (t *Team) SupervisorOf(agent string) string {
	if t == nil {
		return ""
	}
	best, rank := "", 3
	for _, e := range t.Edges {
		if e.Relation == nil || pyStr(e.Relation) != "supervisor" {
			continue
		}
		src, dst := "", ""
		if e.has["from"] {
			src = pyStr(e.From)
		}
		if e.has["to"] {
			dst = pyStr(e.To)
		}
		if strings.Contains(src, "*") || src == agent || src == "human" || !t.Matches(dst, agent) {
			continue
		}
		r := 2
		if dst == agent {
			r = 0
		} else if strings.HasSuffix(dst, ".*") {
			r = 1
		}
		if r < rank {
			best, rank = src, r
		}
	}
	if rank < 3 {
		return best
	}
	if boss := ParentName(agent); boss != "" && t.Known(boss) {
		return boss
	}
	return ""
}
