package reads

import (
	"encoding/json"

	"github.com/handline/handline/go/internal/loadgate"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/usage"
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
		"resources":     e.resources(),
	}, nil
}

// resources is the latest measure for daemon.status: null until the sampler has measured, or on a daemon that does not.
func (e *Env) resources() map[string]any {
	if e.Resources == nil {
		return nil
	}
	r := e.Resources()
	if r == nil {
		return nil
	}
	// Whether new starts are held now, and why: the load gate's own answer on the same measure and limits.
	var hold any
	if e.Machine != nil {
		by, _ := (&usage.Env{Home: e.Home}).ActiveByAccount(e.DB)
		active := 0
		for _, agents := range by {
			active += len(agents)
		}
		if why := loadgate.Held(loadgate.LimitsOf(usage.LoadConfig(e.Home)), active, e.Machine()); why != "" {
			hold = why
		}
	}
	r["start_hold"] = hold
	return r
}
