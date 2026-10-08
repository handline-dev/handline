package structured

// Event is a CLI frame reduced to what a transcript view needs. The daemon publishes them per agent; the sink numbers them.
//
//	state             {state}                              idle, running, requires_action
//	message           {uuid, text}                         the CLI echoed a message: delivered
//	assistant_text    {text}
//	tool_use          {id, name, input}
//	tool_result       {tool_use_id, content, is_error}
//	turn_end          {subtype, is_error, cost_usd, duration_ms, usage}
//	permission_request {prompt, tool, input, reason}       (from the broker)
//	permission_answer  {prompt, allow, said, by}           (from the broker)
type Event struct {
	Type string
	Data map[string]any
}

// EventSink takes an agent's events in order.
type EventSink interface {
	Emit(agent string, ev Event)
}

// Translate reduces one CLI frame to zero or more events. Frames it does not know are dropped, not guessed at.
func Translate(m map[string]any) []Event {
	switch m["type"] {
	case "system":
		if m["subtype"] == "session_state_changed" {
			return []Event{{"state", map[string]any{"state": m["state"]}}}
		}
	case "assistant":
		var out []Event
		for _, b := range blocks(m) {
			switch b["type"] {
			case "text":
				out = append(out, Event{"assistant_text", map[string]any{"text": b["text"]}})
			case "tool_use":
				out = append(out, Event{"tool_use", map[string]any{"id": b["id"], "name": b["name"], "input": b["input"]}})
			}
		}
		return out
	case "user":
		if replay, _ := m["isReplay"].(bool); replay {
			return []Event{{"message", map[string]any{"uuid": m["uuid"], "text": textOf(m)}}}
		}
		var out []Event
		for _, b := range blocks(m) {
			if b["type"] == "tool_result" {
				out = append(out, Event{"tool_result", map[string]any{"tool_use_id": b["tool_use_id"], "content": b["content"], "is_error": b["is_error"] == true}})
			}
		}
		return out
	case "result":
		return []Event{{"turn_end", map[string]any{"subtype": m["subtype"], "is_error": m["is_error"] == true, "cost_usd": m["total_cost_usd"],
			"duration_ms": m["duration_ms"], "usage": m["usage"]}}}
	}
	return nil
}

func blocks(m map[string]any) []map[string]any {
	msg, _ := m["message"].(map[string]any)
	list, _ := msg["content"].([]any)
	var out []map[string]any
	for _, b := range list {
		if bm, ok := b.(map[string]any); ok {
			out = append(out, bm)
		}
	}
	return out
}

func textOf(m map[string]any) string {
	msg, _ := m["message"].(map[string]any)
	if s, ok := msg["content"].(string); ok {
		return s
	}
	for _, b := range blocks(m) {
		if t, ok := b["text"].(string); ok {
			return t
		}
	}
	return ""
}
