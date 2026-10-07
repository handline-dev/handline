package reads

import (
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

// fileInfo is a team.get file entry: its path and when it was last written, null when it cannot be read.
func fileInfo(path string) map[string]any {
	var updated any
	if st, err := os.Stat(path); err == nil {
		updated = ts(st.ModTime().Unix())
	}
	return map[string]any{"path": path, "updated": updated}
}

// teamGet is team.get: every agent with its parent and files, and the allowed edges. A team.yaml that cannot be read is no parents and no edges.
func (e *Env) teamGet(c *rpc.Conn, params map[string]any) (any, error) {
	table, err := team.LoadTeam(e.Home)
	if err != nil {
		table = nil
	}
	agents := []any{}
	for _, name := range e.listAgents() {
		var parent any
		if table != nil {
			if p := table.SupervisorOf(name); p != "" {
				parent = p
			}
		}
		dir := paths.AgentDir(e.Home, name)
		agents = append(agents, map[string]any{"name": name, "role": e.roleLine(name), "parent": parent,
			"role_file": fileInfo(filepath.Join(dir, "ROLE.md")), "base_file": fileInfo(filepath.Join(dir, "base.md"))})
	}
	edges := []any{}
	if table != nil {
		for _, ed := range table.Edges {
			edges = append(edges, map[string]any{"from": pyText(ed.From), "to": pyText(ed.To)})
		}
	}
	return map[string]any{"agents": agents, "edges": edges}, nil
}
