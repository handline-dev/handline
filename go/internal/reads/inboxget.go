package reads

import (
	"strconv"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// inboxGet is inbox.get: one request read whole, seen from the caller, held up to wait_s until it settles (landed, or a state
// that never reached its target). handline/daemon/rpc_inbox_get.py.
func (e *Env) inboxGet(c *rpc.Conn, params map[string]any) (any, error) {
	id, ok := rpc.AsInt(params["id"])
	if !ok {
		return nil, badParam("id is a request number", "id")
	}
	wait, err := optInt(params, "wait_s")
	if err != nil || (wait != nil && (*wait < 0 || *wait > maxWaitS)) {
		return nil, badParam("wait_s is an integer from 0 to 600", "wait_s")
	}
	viewer := human
	if c.Caller.Role == "agent" {
		viewer = c.Caller.Info["name"].(string)
	}
	var until time.Time
	if wait != nil {
		until = time.Now().Add(time.Duration(*wait) * time.Second)
	}
	for {
		r, err := queryOne(e.DB, "SELECT *, "+answerSQL+", "+dismissedSQL+" FROM requests WHERE id = ?", id)
		if err != nil {
			return nil, err
		}
		if r == nil || (viewer == r.str("target") && viewer != human && r.i("edge_ok") == 0) {
			err := rpc.NotFoundError("no request " + strconv.FormatInt(id, 10))
			err.Data = map[string]any{"what": "request", "name": strconv.FormatInt(id, 10)}
			return nil, err
		}
		switch r.str("state") {
		case "landed", "denied", "withdrawn", "replaced":
			return e.itemOf(r, viewer)
		}
		if wait == nil || !time.Now().Before(until) {
			return e.itemOf(r, viewer)
		}
		select {
		case <-c.Done():
			return e.itemOf(r, viewer)
		case <-time.After(pollEvery):
		}
	}
}

// itemOf is the request as inbox.list gives it, the other end named for the viewer.
func (e *Env) itemOf(r row, viewer string) (any, error) {
	other := r.str("target")
	if r.str("target") == viewer {
		other = r.str("requester")
	}
	kind := r.str("kind")
	if kind == "" {
		kind = "ask"
	}
	_, answered := shownVerdict(r)
	return e.item(r.i("id"), kind, other, r.str("body"), r.i("ts"), r.set("read_at") || r.str("state") != "queued", answered)
}
