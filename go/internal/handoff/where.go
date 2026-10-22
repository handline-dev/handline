package handoff

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/textsafe"
)

// Where is the daemon's own account of where an agent was, put under its handoff at every fresh start, or in its place when there is
// none: its open tasks with each one's last memo, its last messages with the founder and its lead, and, with no record, its last
// progress note. All of it is already in the database, so it is there however the last session ended.
const (
	// WhereTasks and WhereMessages are how many of each it lists.
	WhereTasks    = 8
	WhereMessages = 5
	// StaleRecordS is the age past which a record is flagged: the team may have changed since it was written.
	StaleRecordS = 3 * 86400
)

type dbHeads struct{}

func (dbHeads) EffectiveHead(q db.Q, agent string) (*db.Request, error) {
	return db.EffectiveHead(q, agent)
}

func firstLine(s string) string {
	if lines := textsafe.SplitLines(s); len(lines) > 0 {
		return cutRunes(textsafe.Oneline(lines[0]), 120)
	}
	return ""
}

// recordAge is how old the record is by its captured line; ok is false when it has none.
func recordAge(record string, now time.Time) (time.Duration, bool) {
	for _, l := range textsafe.SplitLines(record) {
		if v, found := strings.CutPrefix(l, capturedPrefix); found {
			at, err := time.ParseInLocation(capturedLayout, v, time.Local)
			return now.Sub(at), err == nil
		}
	}
	return 0, false
}

func taskLines(q db.Q, agent string) ([]string, error) {
	ts, err := db.OpenTasksOf(q, agent)
	if err != nil {
		return nil, err
	}
	states := map[int64]string{}
	for _, t := range ts {
		if states[t.ID], err = db.TaskState(q, t); err != nil {
			return nil, err
		}
	}
	sort.SliceStable(ts, func(i, j int) bool { return states[ts[i].ID] == "doing" && states[ts[j].ID] != "doing" }) // what it is on, first
	var lines []string
	for _, t := range ts {
		state := states[t.ID]
		if state == "draft" {
			continue // not handed to it yet
		}
		if len(lines) >= 2*WhereTasks {
			lines = append(lines, "  ... more: hl task list")
			break
		}
		line := fmt.Sprintf("  T%d %s P%d - %s", t.ID, state, t.Priority, cutRunes(textsafe.Oneline(t.Title), 120))
		if ids, err := db.BlocksOnIDs(q, t.ID); err != nil {
			return nil, err
		} else if len(ids) > 0 {
			waits := make([]string, len(ids))
			for i, id := range ids {
				waits[i] = fmt.Sprintf("T%d", id)
			}
			line += " (waits on " + strings.Join(waits, ", ") + ")"
		}
		lines = append(lines, line)
		var author, body string
		var at float64
		switch err := q.QueryRow("SELECT author, body, created_at FROM task_memos WHERE task_id = ? ORDER BY id DESC LIMIT 1", t.ID).Scan(&author, &body, &at); {
		case err == nil:
			lines = append(lines, fmt.Sprintf("    last memo, %s %s: %s", author, time.Unix(int64(at), 0).Format("01-02 15:04"), firstLine(body)))
		case !isNoRows(err):
			return nil, err
		}
	}
	if len(lines) == 0 {
		return []string{"your open tasks: none"}, nil
	}
	return append([]string{"your open tasks:"}, lines...), nil
}

func messageLines(q db.Q, agent, lead string) ([]string, error) {
	rows, err := q.Query("SELECT id, ts, requester, target, body FROM requests WHERE (requester = ? AND target IN (?, ?)) OR (target = ? AND requester IN (?, ?)) "+
		"ORDER BY id DESC LIMIT ?", agent, db.Human, lead, agent, db.Human, lead, WhereMessages)
	if err != nil {
		return nil, err
	}
	type msg struct {
		id, ts         int64
		from, to, body string
	}
	var ms []msg
	for rows.Next() {
		var m msg
		if err := rows.Scan(&m.id, &m.ts, &m.from, &m.to, &m.body); err != nil {
			rows.Close()
			return nil, err
		}
		ms = append(ms, m)
	}
	rows.Close()
	if err := rows.Err(); err != nil || len(ms) == 0 {
		return nil, err
	}
	head := "last messages with the founder"
	if lead != db.Human {
		head += " and " + lead
	}
	lines := []string{head + ", newest first:"}
	name := func(n string) string {
		if n == agent {
			return "you"
		}
		return n
	}
	for _, m := range ms {
		lines = append(lines, fmt.Sprintf("  %s %s %s -> %s: %s", db.MsgName(q, m.id), time.Unix(m.ts, 0).Format("01-02 15:04"), name(m.from), name(m.to), firstLine(m.body)))
	}
	return lines, nil
}

// WhereText is that account for agent, whose lead is lead ("" when the founder is its lead); record is the handoff it is given, "" for
// none.
func WhereText(q db.Q, agent, lead, record string, now time.Time) (string, error) {
	if lead == "" {
		lead = db.Human
	}
	lines := []string{"# Where you were (the daemon's record)"}
	if age, ok := recordAge(record, now); ok && age > StaleRecordS*time.Second {
		lines = append(lines, fmt.Sprintf("Your handoff above is %d days old: run `hl team` first, the team may have changed since.", int(age.Hours()/24)))
	}
	tasks, err := taskLines(q, agent)
	if err != nil {
		return "", err
	}
	lines = append(lines, tasks...)
	msgs, err := messageLines(q, agent, lead)
	if err != nil {
		return "", err
	}
	lines = append(lines, msgs...)
	if record == "" {
		progress, err := (&Env{DB: q, Heads: dbHeads{}}).progressLines(agent)
		if err != nil {
			return "", err
		}
		lines = append(lines, progress...)
	}
	lines = append(lines, "Older messages: `hl inbox --all`, then `hl read <id>`.")
	return strings.Join(lines, "\n"), nil
}
