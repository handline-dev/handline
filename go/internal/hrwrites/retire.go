package hrwrites

import (
	"database/sql"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// retireMethod is agent.retire: the founder retires an agent at once, by the same step an approved retire proposal runs
// (doRetire). Refused while the agent has a session running, or while another agent reports to it or hangs under its name.
func (e *Env) retireMethod(c *rpc.Conn, params map[string]any) (any, error) {
	agent, _ := params["agent"].(string)
	if agent == "" {
		return nil, rpc.BadRequestError("agent is an agent's name", "agent", rpc.InvalidParams)
	}
	everyone := paths.ListAgents(e.Home)
	if !slices.Contains(everyone, agent) {
		err := rpc.NotFoundError("no such agent: " + agent)
		err.Data = map[string]any{"what": "agent", "name": agent}
		return nil, err
	}
	live, ok := map[string]bool(nil), false
	if e.Live != nil {
		live, ok = e.Live()
	}
	if !ok {
		return nil, rpc.Refused("could not confirm whether "+agent+" has a session running just now - try again in a moment.", "state", "")
	}
	if live[agent] {
		return nil, rpc.Refused(agent+" has a session running. Close it first: an agent is not retired under a running session.", "state", "")
	}
	if deps := e.dependents(agent, everyone); len(deps) > 0 {
		return nil, rpc.Refused(fmt.Sprintf("%s has agents under it (%s): retire or move them first.", agent, strings.Join(deps, ", ")), "state", "")
	}
	prop := &Prop{pairs: []yamlcompat.Pair{{Key: "kind", Val: "retire"}, {Key: "agent", Val: agent},
		{Key: "id", Val: fmt.Sprintf("%x", time.Now().UnixNano())}}}
	ok, said, err := e.run(prop)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, rpc.Refused(said, "state", "")
	}
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		return map[string]any{"accepted": true, "said": said}, nil
	})
}

// dependents are the other agents that report to agent (team.yaml's supervisor) or hang under its name (agent.x).
func (e *Env) dependents(agent string, everyone []string) []string {
	t, _ := team.LoadTeam(e.Home)
	var out []string
	for _, n := range everyone {
		if n != agent && (team.ParentName(n) == agent || t.SupervisorOf(n) == agent) {
			out = append(out, n)
		}
	}
	return out
}
