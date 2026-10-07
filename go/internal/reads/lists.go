package reads

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
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

type schedule struct{ cron, message string }

func (s schedule) sid() string {
	h := sha1.Sum([]byte(s.cron + "\n" + s.message))
	return hex.EncodeToString(h[:])[:10]
}

// scheduleID is the integer the spec gives a schedule: ten hex digits of sha1 over the agent and the schedule's sid.
func scheduleID(agent string, s schedule) int64 {
	h := sha1.Sum([]byte(agent + "\n" + s.sid()))
	var n int64
	for _, b := range h[:5] {
		n = n<<8 | int64(b)
	}
	return n
}

// schedulesOf is an agent's `schedules` value, validated as schedules.parse does; an agent.yaml or value that cannot be read is none.
func (e *Env) schedulesOf(agent string) []schedule {
	cfg, err := team.NewRoster(e.Home).Read(agent)
	if err != nil {
		return nil
	}
	var items []any
	switch v := cfg["schedules"].(type) {
	case nil:
		return nil
	case string:
		if v == "" {
			return nil
		}
		if json.Unmarshal([]byte(v), &items) != nil {
			return nil
		}
	case []any:
		items = v
	default:
		return nil
	}
	out := []schedule{}
	for _, it := range items {
		m := map[string]any{}
		switch x := it.(type) {
		case map[string]any:
			m = x
		case *yamlcompat.OMap:
			m = x.M
		default:
			return nil
		}
		expr, hasC := m["cron"]
		msg, hasM := m["message"]
		if len(m) != 2 || !hasC || !hasM {
			return nil
		}
		cronText := strings.Join(strings.Fields(pyText(expr)), " ")
		if _, err := parseCron(cronText); err != nil {
			return nil
		}
		message := strings.Join(strings.Fields(pyText(msg)), " ")
		if message == "" {
			return nil
		}
		out = append(out, schedule{cronText, message})
	}
	return out
}

// firedAt is when a schedule last fired: the newest schedule_fired event of the agent for its cron.
func (e *Env) firedAt(agent string, s schedule) int64 {
	rows, _ := query(e.DB, "SELECT ts, payload FROM events WHERE kind = 'schedule_fired' AND agent = ? ORDER BY id DESC LIMIT 200", agent)
	for _, r := range rows {
		var p map[string]any
		if json.Unmarshal([]byte(r.str("payload")), &p) == nil && p["cron"] == s.cron {
			return r.i("ts")
		}
	}
	return 0
}

// schedulesList is schedules.list: each agent's schedules with the last time one fired and the next time it names.
func (e *Env) schedulesList(c *rpc.Conn, params map[string]any) (any, error) {
	now := time.Now().Unix()
	out := []any{}
	for _, agent := range e.listAgents() {
		for _, s := range e.schedulesOf(agent) {
			last := e.firedAt(agent, s)
			cur, _ := queryOne(e.DB, "SELECT row_id FROM cursors WHERE name = ?", "schedule_at:"+agent+":"+s.sid())
			handled := cur.i("row_id")
			from := max(last, handled)
			if last == 0 && handled == 0 {
				from = now
			}
			var next, lastRun any
			if n, ok := mustCron(s.cron).nextAfter(from); ok {
				next = ts(n)
			}
			if last != 0 {
				lastRun = ts(last)
			}
			out = append(out, map[string]any{"id": scheduleID(agent, s), "target": agent, "cron": s.cron, "message": s.message,
				"next_run": next, "last_run": lastRun, "set_by": "human"})
		}
	}
	return map[string]any{"schedules": out}, nil
}

func mustCron(expr string) *cronExpr {
	c, _ := parseCron(expr)
	return c
}
