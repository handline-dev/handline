package reads

import (
	"encoding/json"

	"github.com/handline/handline/go/internal/operations"
	"github.com/handline/handline/go/internal/rpc"
)

// operationGet is operation.get: an operation's row. A row still `running` whose operation this process is not running was left by a
// daemon that stopped: it is reported `failed`, as Python reports one whose daemon died; the row is not rewritten.
func (e *Env) operationGet(_ *rpc.Conn, params map[string]any) (any, error) {
	op, _ := params["operation"].(string)
	if op == "" {
		return nil, badParam("operation is an operation id", "operation")
	}
	live := operations.Live(op) // asked before the row is read: a thread writes its last state, then leaves
	r, err := queryOne(e.DB, "SELECT * FROM operations WHERE id = ?", op)
	if err != nil {
		return nil, err
	}
	if r == nil {
		nf := rpc.NotFoundError("no operation " + op)
		nf.Data = map[string]any{"what": "operation", "name": op}
		return nil, nf
	}
	state, message := r.str("state"), r.strOrNil("message")
	if state == "running" && !live {
		state, message = "failed", "the daemon stopped before it finished"
	}
	var result any
	if t := r.str("result_json"); t != "" {
		if json.Unmarshal([]byte(t), &result) != nil {
			result = nil
		}
	}
	return map[string]any{"id": r.str("id"), "kind": r.str("kind"), "state": state, "step": r.strOrNil("step"), "steps_done": r.i("steps_done"),
		"steps_total": r.i("steps_total"), "message": message, "result": result, "agent": r.strOrNil("agent")}, nil
}
