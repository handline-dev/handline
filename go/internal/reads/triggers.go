package reads

import (
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

// agentTriggers is agent.triggers (hl triggers): what the next tick would decide for each agent, or for the one named.
func (e *Env) agentTriggers(c *rpc.Conn, params map[string]any) (any, error) {
	names := e.listAgents()
	if params["agent"] != nil {
		name, err := e.named(params)
		if err != nil {
			return nil, err
		}
		names = []string{name}
	}
	roster := team.NewRoster(e.Home)
	out := []map[string]any{}
	for _, name := range names {
		fired, reason, err := e.trigger(roster, name)
		if err != nil {
			return nil, err
		}
		out = append(out, map[string]any{"agent": name, "fired": fired, "reason": reason})
	}
	return map[string]any{"agents": out}, nil
}

func (e *Env) trigger(roster *team.Roster, name string) (bool, string, error) {
	if _, err := roster.Read(name); err != nil {
		return false, "config error: " + oneline(err.Error()), nil
	}
	account, err := team.CurrentAccount(e.DB, name)
	if err != nil {
		return false, "config error: " + oneline(err.Error()), nil
	}
	row, err := db.GetAgentState(e.DB, name)
	if err != nil {
		return false, "", err
	}
	var t Trigger
	if e.Delivery != nil {
		if t, err = e.Delivery.Trigger(e.DB, name, row, account); err != nil {
			return false, "", err
		}
	}
	switch {
	case t.Applies || t.Reason != "":
		return t.Fired, t.Reason, nil
	case row != nil && !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) && (row.Terminal.String != "" || e.SessionOpen != nil && e.SessionOpen(name)):
		return false, strings.ToLower(row.State) + "; judged again at its wake", nil
	}
	return false, "session closed; nothing to hand off", nil
}
