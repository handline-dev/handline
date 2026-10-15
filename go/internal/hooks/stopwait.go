package hooks

import (
	"os"
	"path/filepath"
	"strconv"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// StopWaitReason is actions.stop_wait_reason: why the agent should not stop now - it holds a current task with no recorded wait. "" when
// it holds none, or its task waits on a child, a blocker, a decision of the founder's or its roster proposal.
func StopWaitReason(q db.Q, home, agent string) string {
	tid, has, err := db.CurrentTask(q, agent)
	if err != nil || !has {
		return ""
	}
	if kids, err := db.OpenChildTasks(q, tid); err != nil || len(kids) > 0 {
		return ""
	}
	if blockers, err := db.TaskBlockers(q, tid); err != nil || len(blockers) > 0 {
		return ""
	}
	var open int
	if q.QueryRow("SELECT COUNT(*) FROM tasks t JOIN task_requests l ON l.task_id = t.id JOIN requests r ON r.id = l.request_id "+
		"WHERE t.owner = ? AND t.opened_by = ? AND t.discarded_by IS NULL AND r.state = 'queued'", db.Human, agent).Scan(&open) != nil || open > 0 {
		return ""
	}
	if proposalWaiting(home, agent) {
		return "" // its roster proposal is waiting on the founder
	}
	return "T" + strconv.FormatInt(tid, 10) + " is current with no recorded wait"
}

// proposalWaiting is whether a roster proposal the agent made still waits on the founder: hr/*.yaml with requested_by and state proposed.
func proposalWaiting(home, agent string) bool {
	files, _ := filepath.Glob(filepath.Join(home, "hr", "*.yaml"))
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			continue
		}
		doc, err := yamlcompat.Parse(string(raw))
		m, ok := doc.(map[string]any)
		if err != nil || !ok {
			continue
		}
		if by, _ := m["requested_by"].(string); by == agent {
			if state, _ := m["state"].(string); state == "proposed" {
				return true
			}
		}
	}
	return false
}
