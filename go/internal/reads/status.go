package reads

import (
	"encoding/json"

	"github.com/handline/handline/go/internal/rpc"
)

func (e *Env) status(c *rpc.Conn, params map[string]any) (any, error) {
	stored, err := e.storedViews()
	if err != nil {
		return nil, err
	}
	names := e.listAgents()
	byState := map[string]int{}
	for _, n := range names {
		state := noView
		if v, ok := stored[n]; ok {
			var view struct {
				State string `json:"state"`
			}
			if json.Unmarshal(v, &view) == nil && view.State != "" {
				state = view.State
			}
		}
		byState[state]++
	}
	items, err := e.founderItems()
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"alive": true, "version": c.Server().Sha, "protocol": rpc.Protocol, "schema": c.Server().Schema,
		"started_at": ts(e.Started.Unix()),
		// This daemon drives no terminal host yet.
		"terminal_host": map[string]any{"name": "none", "health": "down", "detail": "this daemon does not drive a terminal host yet"},
		"agents":        map[string]any{"total": len(names), "by_state": byState},
		"inbox":         map[string]any{"unread": len(items)},
	}, nil
}
