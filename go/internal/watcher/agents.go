package watcher

import (
	"cmp"
	"database/sql"
	"encoding/json"
	"slices"

	"github.com/handline/handline/go/internal/stream"
)

// Agents is the agents topic: a changed agent_view row is agent.changed (the view as data), one that turned stalled also says so on the
// notifications topic, and a row gone is agent.removed. It reads the table whoever wrote the row. Its first look says nothing.
type Agents struct {
	seen  map[string]string
	query string // tests: a read that fails part way
}

func (*Agents) Name() string { return "agents" }

func stateOf(view string) string {
	var v struct {
		State string `json:"state"`
	}
	json.Unmarshal([]byte(view), &v)
	return v.State
}

type needs struct{ Title, Text, Source string }

// needsOf is the view's needs_you title, text and source, empty when it has none.
func needsOf(view string) (n needs) {
	var v struct {
		NeedsYou *struct {
			Title, Source string
			Text          *string
		} `json:"needs_you"`
	}
	if json.Unmarshal([]byte(view), &v) == nil && v.NeedsYou != nil {
		n.Title, n.Source = v.NeedsYou.Title, v.NeedsYou.Source
		if v.NeedsYou.Text != nil {
			n.Text = *v.NeedsYou.Text
		}
	}
	return n
}

// stuck is whether a view is a stall the founder is told of: stalled, or waiting on a card the daemon raised (a dialog it will not or
// cannot get past, an agent it could not heal), which the view shows as waiting.
func stuck(view string) bool { return stateOf(view) == "stalled" || needsOf(view).Source == "daemon" }

func (a *Agents) Scan(q *sql.DB, emit Emit) error {
	rows, err := q.Query(cmp.Or(a.query, "SELECT agent, view FROM agent_view"))
	if err != nil {
		return err
	}
	defer rows.Close()
	now := map[string]string{}
	for rows.Next() {
		var name, view string
		if err := rows.Scan(&name, &view); err != nil {
			return err
		}
		now[name] = view
	}
	if err := rows.Err(); err != nil { // a read that stopped short is not a list with agents gone
		return err
	}
	before := a.seen
	a.seen = now
	if before == nil {
		return nil
	}
	names := make([]string, 0, len(now))
	for name := range now {
		names = append(names, name)
	}
	slices.Sort(names)
	for _, name := range names {
		view := now[name]
		if before[name] == view {
			continue
		}
		target := map[string]any{"screen": "agent", "id": name}
		if _, err := emit(stream.Entry{Topic: "agents", Type: "agent.changed", Data: json.RawMessage(view), Agent: name, Target: target}); err != nil {
			return err
		}
		if old, had := before[name]; stuck(view) && (!had || !stuck(old)) {
			title, data := name+" stalled", map[string]any{"agent": name}
			if n := needsOf(view); n.Title != "" {
				title, data["title"] = name+": "+n.Title, n.Title
				if n.Text != "" {
					data["text"] = n.Text
				}
			}
			if _, err := emit(stream.Entry{Topic: "notifications", Type: "stall", Data: data, Agent: name, Title: title, Target: target}); err != nil {
				return err
			}
		}
	}
	gone := make([]string, 0)
	for name := range before {
		if _, here := now[name]; !here {
			gone = append(gone, name)
		}
	}
	slices.Sort(gone)
	for _, name := range gone {
		if _, err := emit(stream.Entry{Topic: "agents", Type: "agent.removed", Data: map[string]any{"name": name}, Agent: name}); err != nil {
			return err
		}
	}
	return nil
}
