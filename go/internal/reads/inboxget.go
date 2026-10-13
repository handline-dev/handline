package reads

import (
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/supervisor"
)

// inboxGet is inbox.get: one request read whole, seen from the caller, held up to wait_s until it settles (landed, or a state
// that never reached its target). handline/daemon/rpc_inbox_get.py.
func (e *Env) inboxGet(c *rpc.Conn, params map[string]any) (any, error) {
	r, viewer, err := e.settledRequest(c, params)
	if err != nil {
		return nil, err
	}
	return e.itemOf(r, viewer)
}

// settledRequest is the request params names and who reads it, held up to wait_s while it is still queued. One the reader may not see is
// not_found: a request to an agent that the routing table has not passed yet is not that agent's to read.
func (e *Env) settledRequest(c *rpc.Conn, params map[string]any) (row, string, error) {
	id, ok := rpc.AsInt(params["id"])
	if !ok {
		return nil, "", badParam("id is a request number", "id")
	}
	wait, err := optInt(params, "wait_s")
	if err != nil || (wait != nil && (*wait < 0 || *wait > maxWaitS)) {
		return nil, "", badParam("wait_s is an integer from 0 to 600", "wait_s")
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
			return nil, "", err
		}
		if r == nil || (viewer == r.str("target") && viewer != human && r.i("edge_ok") == 0) {
			err := rpc.NotFoundError("no request " + strconv.FormatInt(id, 10))
			err.Data = map[string]any{"what": "request", "name": strconv.FormatInt(id, 10)}
			return nil, "", err
		}
		switch r.str("state") {
		case "landed", "denied", "withdrawn", "replaced":
			return r, viewer, nil
		}
		if wait == nil || !time.Now().Before(until) {
			return r, viewer, nil
		}
		select {
		case <-c.Done():
			return r, viewer, nil
		case <-time.After(pollEvery):
		}
	}
}

// requestRead is request.read (hl read): one request whole, as cmd_read in handline/cli/ask.py prints it. The founder reading it is the
// founder opening it (actions.opened_by_human).
func (e *Env) requestRead(c *rpc.Conn, params map[string]any) (any, error) {
	r, viewer, err := e.settledRequest(c, params)
	if err != nil {
		return nil, err
	}
	id := r.i("id")
	if viewer == human {
		if err := db.MarkRead(e.DB, []int64{id}); err != nil {
			return nil, err
		}
		if r.str("requester") == human && r.str("state") != "queued" { // an answer to the founder: read is told
			if err := db.MarkAnnounced(e.DB, []int64{id}); err != nil {
				return nil, err
			}
		}
	}
	kind := r.str("kind")
	if kind == "" {
		kind = "ask"
	}
	out := map[string]any{
		"id": id, "requester": r.str("requester"), "target": r.str("target"), "kind": kind, "state": r.str("state"), "created": ts(r["ts"]),
		"relation": r.strOrNil("relation"), "response": r.strOrNil("response"), "chain_depth": r.i("chain_depth"), "parent": nil,
		"body": r.str("body"), "answer": nil, "dismissal": nil,
	}
	if r.set("parent_id") {
		parent, err := queryOne(e.DB, "SELECT id, body FROM requests WHERE id = ?", r.i("parent_id"))
		if err != nil {
			return nil, err
		}
		if parent != nil {
			out["parent"] = map[string]any{"id": parent.i("id"), "line": parentLine(parent.str("body"))}
		}
	}
	if dismissed, by, reason := dismissal(r); dismissed {
		out["dismissal"] = map[string]any{"by": by, "reason": reason}
	} else if text, has := r["answer"].(string); has && text != "" {
		out["answer"] = text
	}
	return out, nil
}

// parentLine is the start of a request's first line, as delivery.in_reply_to quotes it.
func parentLine(body string) string {
	if i := strings.IndexAny(body, "\n\r\v\f\x1c\x1d\x1e\u0085\u2028\u2029"); i >= 0 { // str.splitlines' breaks
		body = body[:i]
	}
	first := []rune(strings.TrimSpace(oneline(body)))
	if len(first) > supervisor.AskedShown {
		return strings.TrimRightFunc(string(first[:supervisor.AskedShown]), unicode.IsSpace) + "…"
	}
	return string(first)
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
