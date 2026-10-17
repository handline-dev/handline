package reads

import "github.com/handline/handline/go/internal/rpc"

// taskViews is task.view.list: the saved views, by name, each a filter tasks.list takes as it is.
func (e *Env) taskViews(c *rpc.Conn, params map[string]any) (any, error) {
	rows, err := query(e.DB, "SELECT name, filter, updated_by, updated_at FROM task_views ORDER BY name")
	if err != nil {
		return nil, err
	}
	views := make([]any, len(rows))
	for i, r := range rows {
		views[i] = map[string]any{"name": r.str("name"), "filter": decodeObject(r.str("filter")), "by": r.str("updated_by"), "at": ts(r["updated_at"])}
	}
	return map[string]any{"views": views}, nil
}
