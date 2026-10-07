package reads

import (
	"encoding/json"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/stream"
)

const (
	feedDefault = 50
	feedMax     = 200
	prunedName  = "stream:pruned"
)

// jsonValue is a stored JSON text as a value, numbers kept as written; nil for none or garbage.
func jsonValue(s string) any {
	if s == "" {
		return nil
	}
	d := json.NewDecoder(strings.NewReader(s))
	d.UseNumber()
	var v any
	if d.Decode(&v) != nil {
		return nil
	}
	return v
}

func eventOf(r row) map[string]any {
	return map[string]any{"cursor": r.i("id"), "topic": r.str("topic"), "type": r.str("type"), "at": ts(r["ts"]),
		"actor": jsonValue(r.str("actor")), "agent": r.strOrNil("agent"), "title": r.strOrNil("title"),
		"target": jsonValue(r.str("target")), "data": jsonValue(r.str("data"))}
}

// eventsFeed is events.feed: the newest events of the topics, or the ones after a cursor, with the cursor to resume from
// and whether retention has dropped what the cursor asked for (handline/daemon/rpc_stream.py).
func (e *Env) eventsFeed(c *rpc.Conn, params map[string]any) (any, error) {
	topics := []string{"notifications"}
	if v, has := params["topics"]; has && v != nil {
		list, ok := v.([]any)
		if !ok || len(list) == 0 {
			return nil, badParam("topics is a list of topic names", "topics")
		}
		topics = topics[:0]
		for _, t := range list {
			s, ok := t.(string)
			if !ok {
				return nil, badParam("topics is a list of topic names", "topics")
			}
			topics = append(topics, s)
		}
		for _, t := range topics {
			if !stream.Daemon(t) {
				return nil, badParam("no such topic: "+t, "topics")
			}
		}
	}
	var since *int64
	if v, has := params["since"]; has && v != nil {
		n, ok := rpc.AsInt(v)
		if !ok || n < 0 {
			return nil, badParam("since is a cursor, a non-negative integer", "since")
		}
		since = &n
	}
	limit := int64(feedDefault)
	if v, has := params["limit"]; has {
		n, ok := rpc.AsInt(v)
		if !ok || n < 1 {
			return nil, badParam("limit is a positive integer", "limit")
		}
		limit = min(n, feedMax)
	}
	marks := strings.TrimSuffix(strings.Repeat("?,", len(topics)), ",")
	args := make([]any, len(topics))
	for i, t := range topics {
		args[i] = t
	}
	newest, err := queryOne(e.DB, "SELECT COALESCE(MAX(id), 0) AS n FROM stream_log")
	if err != nil {
		return nil, err
	}
	var rows []row
	gap := false
	if since == nil {
		rows, err = query(e.DB, "SELECT * FROM (SELECT * FROM stream_log WHERE topic IN ("+marks+") ORDER BY id DESC LIMIT ?) ORDER BY id",
			append(args, limit)...)
	} else {
		pr, perr := queryOne(e.DB, "SELECT row_id FROM cursors WHERE name = ?", prunedName)
		if perr != nil {
			return nil, perr
		}
		pruned := pr.i("row_id")
		gap = *since < pruned
		rows, err = query(e.DB, "SELECT * FROM stream_log WHERE id > ? AND topic IN ("+marks+") ORDER BY id LIMIT ?",
			append(append([]any{max(*since, pruned)}, args...), limit)...)
	}
	if err != nil {
		return nil, err
	}
	events := make([]any, len(rows))
	for i, r := range rows {
		events[i] = eventOf(r)
	}
	cursor := newest.i("n")
	if since != nil {
		cursor = max(*since, cursor)
	}
	if len(rows) > 0 {
		cursor = rows[len(rows)-1].i("id")
	}
	return map[string]any{"events": events, "cursor": cursor, "gap": gap}, nil
}

// promptList is prompt.list: the agent's open prompt, if the screen watch has refreshed it lately.
func (e *Env) promptList(c *rpc.Conn, params map[string]any) (any, error) {
	name, err := e.named(params)
	if err != nil {
		return nil, err
	}
	p, err := e.openPrompt(name)
	if err != nil {
		return nil, err
	}
	if p == nil {
		return map[string]any{"prompts": []any{}}, nil
	}
	return map[string]any{"prompts": []any{p}}, nil
}
