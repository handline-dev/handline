package hooks

import (
	"database/sql"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// Recorder serves hook.record and the spool over one database and home.
type Recorder struct {
	DB   *sql.DB
	Home string
	Log  func(format string, args ...any)
}

// SpoolEvery is how often the spool is looked at while the daemon runs, as the Python daemon does each tick.
const SpoolEvery = 2 * time.Second

// Register adds hook.record to the server's table.
func (h *Recorder) Register(s *rpc.Server) {
	s.Register("hook.record", rpc.Method{Role: "agent", Handler: h.record, Write: true, OwnEvent: true})
}

func (h *Recorder) record(c *rpc.Conn, params map[string]any) (any, error) {
	agent, ok := params["agent"].(string)
	if !ok || agent == "" {
		return nil, rpc.BadRequestError("agent is a name", "agent", rpc.InvalidParams)
	}
	kind, ok := params["kind"].(string)
	if !ok || kind == "" {
		return nil, rpc.BadRequestError("kind is a hook event name", "kind", rpc.InvalidParams)
	}
	data, ok := params["data"].(map[string]any)
	if !ok {
		return nil, rpc.BadRequestError("data is the hook's own object", "data", rpc.InvalidParams)
	}
	at, ok := rpc.AsInt(params["at"])
	if !ok {
		return nil, rpc.BadRequestError("at is when the hook ran, in epoch seconds", "at", rpc.InvalidParams)
	}
	if err := c.RequireSelf(agent); err != nil {
		return nil, err
	}
	if !safeName(agent) {
		return nil, rpc.BadRequestError("agent is not a name this daemon keeps files for", "agent", rpc.InvalidParams)
	}
	if err := Apply(h.DB, h.Home, agent, kind, data, params["request_id"].(string), at); err != nil {
		return nil, err
	}
	return map[string]any{}, nil
}
