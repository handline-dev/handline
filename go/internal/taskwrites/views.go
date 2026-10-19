package taskwrites

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// taskStates are the states tasks.list filters on.
var taskStates = []string{"draft", "queued", "doing", "review", "done", "discarded"}

// viewName is the name a saved view is kept under: one line, 1 to 60 characters.
func viewName(p map[string]any) (string, error) {
	s, _ := p["name"].(string)
	name := strings.TrimSpace(textsafe.Oneline(s))
	if name == "" || len([]rune(name)) > 60 {
		return "", badParam("name is a view's name: one line of 1 to 60 characters", "name")
	}
	return name, nil
}

// viewFilter is a saved view's filter as it is kept: the tasks.list params it holds (tags, state, owner, reporter, from), checked, as JSON.
func viewFilter(p map[string]any) (string, error) {
	f, ok := p["filter"].(map[string]any)
	if !ok {
		return "", badParam("filter is an object of tasks.list params", "filter")
	}
	out := map[string]any{}
	for k, v := range f {
		switch k {
		case "tags":
			tags, err := TagList(f, k)
			if err != nil {
				return "", badParam(err.Error(), "filter")
			}
			if len(tags) > 0 {
				out[k] = tags
			}
		case "state":
			s, _ := v.(string)
			if !contains(taskStates, s) {
				return "", badParam("filter.state is one of "+strings.Join(taskStates, ", "), "filter")
			}
			out[k] = s
		case "owner", "reporter", "from":
			s, _ := v.(string)
			if strings.TrimSpace(s) == "" {
				return "", badParam("filter."+k+" is a name", "filter")
			}
			out[k] = s
		default:
			return "", badParam("filter has no "+k+": it holds tags, state, owner, reporter and from", "filter")
		}
	}
	b, err := json.Marshal(out)
	return string(b), err
}

// viewSave is task.view.save: the filter kept under the name, replacing one already kept there.
func viewSave(c *rpc.Conn, p map[string]any) (any, error) {
	name, err := viewName(p)
	if err != nil {
		return nil, err
	}
	filter, err := viewFilter(p)
	if err != nil {
		return nil, err
	}
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if _, err := tx.Exec("INSERT INTO task_views(name, filter, updated_by, updated_at) VALUES(?, ?, ?, ?) "+
			"ON CONFLICT(name) DO UPDATE SET filter = excluded.filter, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
			name, filter, c.Caller.Actor(), time.Now().Unix()); err != nil {
			return nil, err
		}
		return accepted(fmt.Sprintf("view %q saved", name), nil), nil
	})
}

// viewDelete is task.view.delete.
func viewDelete(c *rpc.Conn, p map[string]any) (any, error) {
	name, err := viewName(p)
	if err != nil {
		return nil, err
	}
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		res, err := tx.Exec("DELETE FROM task_views WHERE name = ?", name)
		if err != nil {
			return nil, err
		}
		if n, err := res.RowsAffected(); err != nil {
			return nil, err
		} else if n == 0 {
			e := rpc.NotFoundError(fmt.Sprintf("no view %q", name))
			e.Data = map[string]any{"what": "view", "name": name}
			return nil, e
		}
		return accepted(fmt.Sprintf("view %q deleted", name), nil), nil
	})
}
