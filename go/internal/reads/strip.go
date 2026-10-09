package reads

import (
	"encoding/json"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/states"
)

// agent.strip follows handline/daemon/rpc_uireads.py: the line under an agent's chat header. The state, its detail, the account and the
// cache's end come from the stored agent view; the model and the context figures from Context.

// screenWord is the screen word behind a view's wire state (agentview's wire, read back); a view with no state has none.
func screenWord(state, detail string) (word, shown string) {
	switch state {
	case "running":
		return "RUNNING", detail
	case "waiting":
		return "DIALOG", detail
	case "stalled":
		return "FATAL", detail
	case "parked":
		if detail == states.MovingMark {
			return "MOVING", ""
		}
		return "PENDING", detail
	case "queued":
		return "PENDING", detail
	}
	return "IDLE", detail
}

func (e *Env) agentStrip(c *rpc.Conn, params map[string]any) (any, error) {
	name, err := e.chatAgent(params, true)
	if err != nil {
		return nil, err
	}
	stored, err := e.storedViews()
	if err != nil {
		return nil, err
	}
	raw, err := e.viewOf(name, stored)
	if err != nil {
		return nil, err
	}
	var view struct {
		Account    string  `json:"account"`
		State      string  `json:"state"`
		Detail     string  `json:"state_detail"`
		CacheUntil *string `json:"cache_until"`
	}
	if err := json.Unmarshal(raw, &view); err != nil {
		return nil, err
	}
	row, err := queryOne(e.DB, "SELECT 1 FROM agent_state WHERE agent = ?", name)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"agent": name, "model": nil, "account": nil, "ctx_pct": nil, "ctx_used": nil, "ctx_limit": nil,
		"cache_left_s": nil, "screen_word": nil, "screen_detail": ""}
	if row != nil {
		word, detail := screenWord(view.State, view.Detail)
		out["screen_word"], out["screen_detail"] = word, detail
	}
	if view.CacheUntil != nil {
		if until, err := time.Parse("2006-01-02T15:04:05Z", *view.CacheUntil); err == nil {
			out["cache_left_s"] = until.Unix() - time.Now().Unix()
		}
	}
	if view.Account == "" {
		return out, nil
	}
	out["account"] = view.Account
	if e.Context == nil {
		return out, nil
	}
	model, used, limit := e.Context(name, view.Account)
	if model != "" {
		out["model"] = model
	}
	if used != nil {
		out["ctx_used"] = *used
		if limit > 0 {
			out["ctx_pct"] = int(*used * 100 / int64(limit))
		}
	}
	if limit > 0 {
		out["ctx_limit"] = limit
	}
	return out, nil
}
