package team

// SupervisorOf is the agent this one answers to: the agent its name hangs under (`harbor.sea` answers to `harbor`) when that agent is
// known, else the manager of its team, the member the team is named after.
func (t *Team) SupervisorOf(agent string) string {
	if t == nil {
		return ""
	}
	if boss := ParentName(agent); boss != "" && t.Known(boss) {
		return boss
	}
	return t.ManagerOf(agent)
}
