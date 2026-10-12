package hrwrites

import (
	"database/sql"

	"github.com/handline/handline/go/internal/rpc"
)

// agentSetMethod is agent.set: the founder changes any setting of any agent, an agent only the ordinary ones of itself.
func (e *Env) agentSetMethod(c *rpc.Conn, params map[string]any) (any, error) {
	bad := func(message, field string) error { return rpc.BadRequestError(message, field, rpc.InvalidParams) }
	agent, _ := params["agent"].(string)
	if agent == "" {
		return nil, bad("agent is an agent's name", "agent")
	}
	raw, isMap := params["values"].(map[string]any)
	if !isMap || len(raw) == 0 {
		return nil, bad("values is the settings to change, key to text", "values")
	}
	values := make(map[string]string, len(raw))
	for k, v := range raw {
		s, isText := v.(string)
		if !isText {
			return nil, bad(k+" is text, as `hl agent set` takes it", "values")
		}
		values[k] = s
	}
	var ok bool
	var said string
	if c.Caller.Role == "agent" {
		if err := c.RequireSelf(agent); err != nil {
			return nil, err
		}
		ok, said = e.SetByAgent(agent, values, agent)
	} else {
		ok, said = e.roster().SetValues(e.DB, agent, values, "", idleMinutes, idleClose{e})
	}
	if !ok {
		return nil, rpc.Refused(said, "", "")
	}
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		return map[string]any{"accepted": true, "said": said}, nil
	})
}
