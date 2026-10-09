package reads

import (
	"encoding/json"
	"sort"
)

// What the chat topic's events carry is what the chat reads return, built by the same functions: these are the entry points the watcher
// calls with the ids it found changed.

// ChatRequest is one request as chat.messages shows it, and the transcript items its replies make for an agent's chat.
type ChatRequest struct {
	Message           map[string]any
	Requester, Target string
	r                 row
}

// Replies is what agent said in this request that chat.messages does not serve (chat.transcript's reply items).
func (c *ChatRequest) Replies(agent string) []map[string]any { return repliesOf(c.r, agent) }

// ChatRequest reads request id whether or not any chat draws it; nil when there is none.
func (e *Env) ChatRequest(id int64) (*ChatRequest, error) {
	ch := &chat{e: e}
	r, err := ch.requestRow(id)
	if err != nil || r == nil {
		return nil, err
	}
	m, err := ch.messageOf(r)
	if err != nil {
		return nil, err
	}
	return &ChatRequest{Message: m, Requester: r.str("requester"), Target: r.str("target"), r: r}, nil
}

// TaskEventItem is the task event with that id as a transcript item, and the owner of the task it names; ok is false when the chat does
// not show the event or its task is gone.
func (e *Env) TaskEventItem(id int64) (item map[string]any, owner string, ok bool, err error) {
	ev, err := queryOne(e.DB, "SELECT id, ts, agent, kind, payload FROM events WHERE id = ?", id)
	if err != nil || ev == nil {
		return nil, "", false, err
	}
	item, tid, shown := taskItemOf(ev)
	if !shown {
		return nil, "", false, nil
	}
	t, err := queryOne(e.DB, "SELECT owner FROM tasks WHERE id = ?", tid)
	if err != nil || t == nil {
		return nil, "", false, err
	}
	return item, t.str("owner"), true, nil
}

// PromptItem is the prompt block with that id as a transcript item, and the agent it belongs to; ok is false when there is no such block.
func (e *Env) PromptItem(id int64) (item map[string]any, agent string, ok bool, err error) {
	b, err := queryOne(e.DB, "SELECT * FROM prompt_blocks WHERE id = ?", id)
	if err != nil || b == nil {
		return nil, "", false, err
	}
	return promptItemOf(b.str("agent"), b), b.str("agent"), true, nil
}

// WorkingOf is a stored agent view (the agent_view row's JSON) as the spec's Working.
func WorkingOf(view string) map[string]any {
	var v map[string]any
	if json.Unmarshal([]byte(view), &v) != nil {
		v = map[string]any{}
	}
	return workingFrom(v)
}

// ChatTaskKinds are the kinds of task event the chat shows, sorted.
func ChatTaskKinds() []string {
	kinds := make([]string, 0, len(transcriptEvents))
	for k := range transcriptEvents {
		kinds = append(kinds, k)
	}
	sort.Strings(kinds)
	return kinds
}
