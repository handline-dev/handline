package watcher

import (
	"database/sql"
	"encoding/json"
	"slices"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

// The chat topic's cursors are the Python watcher's, so either side picks up where the other left off.
const (
	chatTouched = "stream:chat_touched"
	chatEvents  = "stream:chat_events"
	chatPrompts = "stream:chat_prompts"
)

// A request is touched when it changed (state_at, else its own ts) or an answer to it was said (a row with settles = its id).
const chatTouchedSQL = "SELECT id, MAX(t) AS touched FROM (SELECT id, COALESCE(state_at, ts) AS t FROM requests WHERE settles IS NULL AND COALESCE(state_at, ts) >= ? " +
	"UNION ALL SELECT settles, ts FROM requests WHERE settles IS NOT NULL AND ts >= ?) GROUP BY id ORDER BY touched, id"

const chatTouchedNewest = "SELECT COALESCE(MAX(m), 0) FROM (SELECT MAX(COALESCE(state_at, ts)) AS m FROM requests WHERE settles IS NULL " +
	"UNION ALL SELECT MAX(ts) FROM requests WHERE settles IS NOT NULL)"

// Chat is the chat topic: a request of a chat that appeared or changed (chat.message, with the replies its chat shows as chat.transcript),
// a prompt an agent opened or closed and a task event of a task it owns (chat.transcript), and an agent's working state (chat.working).
// Each is said once, to the chat of every agent it belongs to, and its data is what the chat reads return (reads.ChatRequest and the
// other builders). The first look says nothing, and neither does a restart: the requests touched in the second the cursor is at are
// taken as seen.
type Chat struct {
	Reads   *reads.Env
	done    map[int64]string // request -> the message as last said, for the ones touched in the second the cursor is at
	open    map[int64]bool   // the prompts told open and not yet told closed
	working map[string]string
}

func (*Chat) Name() string { return "chat" }

func encoded(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func agentTarget(name string) map[string]any { return map[string]any{"screen": "agent", "id": name} }

func (c *Chat) say(emit Emit, typ, agent string, data any) error {
	_, err := emit(stream.Entry{Topic: "chat", Type: typ, Data: data, Agent: agent, Target: agentTarget(agent)})
	return err
}

func (c *Chat) Scan(q *sql.DB, emit Emit) error {
	if err := c.requests(q, emit); err != nil {
		return err
	}
	if err := c.taskEvents(q, emit); err != nil {
		return err
	}
	if err := c.prompts(q, emit); err != nil {
		return err
	}
	return c.workingStates(q, emit)
}

func (c *Chat) requests(q *sql.DB, emit Emit) error {
	last, err := Prime(q, chatTouched, chatTouchedNewest)
	if err != nil {
		return err
	}
	first := c.done == nil
	if first {
		c.done = map[int64]string{}
	}
	rows, err := q.Query(chatTouchedSQL, last, last)
	if err != nil {
		return err
	}
	type touch struct{ id, at int64 }
	var touched []touch
	for rows.Next() {
		var t touch
		if err := rows.Scan(&t.id, &t.at); err != nil {
			rows.Close()
			return err
		}
		touched = append(touched, t)
	}
	if err := rows.Close(); err != nil {
		return err
	}
	for _, t := range touched {
		req, err := c.Reads.ChatRequest(t.id)
		if err != nil {
			return err
		}
		if req == nil {
			continue
		}
		message := encoded(req.Message)
		if c.done[t.id] == message {
			continue
		}
		if first && t.at == last { // said before the restart, or by the Python watcher: seen, not news
			c.done[t.id] = message
			continue
		}
		var told []string
		for _, agent := range []string{req.Target, req.Requester} {
			if agent == db.Human || slices.Contains(told, agent) {
				continue
			}
			told = append(told, agent)
			if err := c.say(emit, "chat.message", agent, req.Message); err != nil {
				return err
			}
			for _, item := range req.Replies(agent) {
				if err := c.say(emit, "chat.transcript", agent, item); err != nil {
					return err
				}
			}
		}
		if t.at > last {
			last, c.done = t.at, map[int64]string{}
		}
		c.done[t.id] = message
		if err := db.SetCursor(q, chatTouched, last); err != nil {
			return err
		}
	}
	return nil
}

func (c *Chat) taskEvents(q *sql.DB, emit Emit) error {
	seen, err := Prime(q, chatEvents, "SELECT COALESCE(MAX(id), 0) FROM events")
	if err != nil {
		return err
	}
	kinds := reads.ChatTaskKinds()
	args := []any{seen}
	marks := ""
	for i, k := range kinds {
		if i > 0 {
			marks += ","
		}
		marks += "?"
		args = append(args, k)
	}
	ids, err := ids(q, "SELECT id FROM events WHERE id > ? AND kind IN ("+marks+") ORDER BY id", args...)
	if err != nil {
		return err
	}
	for _, id := range ids {
		item, owner, ok, err := c.Reads.TaskEventItem(id)
		if err != nil {
			return err
		}
		if ok && owner != "" && owner != db.Human {
			if err := c.say(emit, "chat.transcript", owner, item); err != nil {
				return err
			}
		}
		if err := db.SetCursor(q, chatEvents, id); err != nil { // per row: an emit that fails does not repeat the ones before it
			return err
		}
	}
	return nil
}

func (c *Chat) prompts(q *sql.DB, emit Emit) error {
	shown, err := Prime(q, chatPrompts, "SELECT COALESCE(MAX(id), 0) FROM prompt_blocks")
	if err != nil {
		return err
	}
	if c.open == nil { // a prompt open before this daemon started is told closed when it closes
		c.open = map[int64]bool{}
		open, err := ids(q, "SELECT id FROM prompt_blocks WHERE gone_at IS NULL AND id <= ?", shown)
		if err != nil {
			return err
		}
		for _, id := range open {
			c.open[id] = true
		}
	}
	fresh, err := ids(q, "SELECT id FROM prompt_blocks WHERE id > ? ORDER BY id", shown)
	if err != nil {
		return err
	}
	for _, id := range fresh {
		item, agent, ok, err := c.Reads.PromptItem(id)
		if err != nil {
			return err
		}
		if ok {
			if err := c.say(emit, "chat.transcript", agent, item); err != nil {
				return err
			}
			if item["open"] == true {
				c.open[id] = true
			}
		}
		if err := db.SetCursor(q, chatPrompts, id); err != nil {
			return err
		}
	}
	held := make([]int64, 0, len(c.open))
	for id := range c.open {
		held = append(held, id)
	}
	slices.Sort(held)
	for _, id := range held {
		item, agent, ok, err := c.Reads.PromptItem(id)
		if err != nil {
			return err
		}
		if ok && item["open"] == true {
			continue
		}
		delete(c.open, id)
		if ok {
			if err := c.say(emit, "chat.transcript", agent, item); err != nil {
				return err
			}
		}
	}
	return nil
}

func (c *Chat) workingStates(q *sql.DB, emit Emit) error {
	rows, err := q.Query("SELECT agent, view FROM agent_view ORDER BY agent")
	if err != nil {
		return err
	}
	var names []string
	now := map[string]map[string]any{}
	for rows.Next() {
		var name, view string
		if err := rows.Scan(&name, &view); err != nil {
			rows.Close()
			return err
		}
		names = append(names, name)
		now[name] = reads.WorkingOf(view)
	}
	if err := rows.Close(); err != nil {
		return err
	}
	before := c.working
	c.working = map[string]string{}
	for _, name := range names {
		c.working[name] = encoded(now[name])
	}
	if before == nil {
		return nil
	}
	for _, name := range names {
		if before[name] != c.working[name] {
			if err := c.say(emit, "chat.working", name, now[name]); err != nil {
				return err
			}
		}
	}
	return nil
}

func ids(q *sql.DB, query string, args ...any) ([]int64, error) {
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, id)
	}
	return out, rows.Err()
}
