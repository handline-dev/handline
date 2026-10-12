package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
)

// Edited is what a committed task.edit leaves for the telling layer.
type Edited struct {
	Task     int64
	By       string
	Body     bool   // a body was given: the holder of a doing task is told it changed
	WasOwner string // the owner before the edit, when an owner was given: a task that changed hands is sent on
	Freed    string // the owner whose last blocker the edit cleared: its turn is reconsidered
}

// pyRepr is Python's repr of a string: single quotes unless it holds one and no double quote.
func pyRepr(s string) string {
	q := byte('\'')
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		q = '"'
	}
	var b strings.Builder
	b.WriteByte(q)
	for _, r := range s {
		switch {
		case r == rune(q) || r == '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r < 0x20 || r == 0x7f:
			fmt.Fprintf(&b, `\x%02x`, r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte(q)
	return b.String()
}

func priorityRef(n int64) string { return fmt.Sprintf("P%d", n) }

type change struct {
	key      string
	old, new string
	body     bool
}

// undeliverable is why task t cannot be moved to agent to, or "": the move asks to on behalf of whoever asked the old owner, or the
// tick does as whoever queued it, and with no edge from them to to that ask is denied and the task sits unread (Supervisor.sendMovedTask).
func undeliverable(tx *sql.Tx, t *task, to, home string) (string, error) {
	asker := t.QueuedBy.String
	live, err := db.LiveDelivery(tx, t.ID, t.Owner)
	if err != nil {
		return "", err
	}
	if live != nil {
		asker = live.Requester
	}
	if asker == "" || asker == to {
		return "", nil
	}
	tm, err := team.LoadTeam(home)
	if err != nil {
		return "", err
	}
	if d := tm.Route(asker, to, 0, false); !d.Allowed {
		return fmt.Sprintf("T%d cannot go to %s: its brief is sent on %s's behalf, and %s", t.ID, to, asker, d.Reason), nil
	}
	return "", nil
}

// editTask is actions.edit_task: a task's owner, reporter, title, priority, timeout and/or body changed by whoever opened it, its reporter
// or the founder, and the owner alone also by the owner's manager. It answers the refusal ("" when it edited), the message and the fields
// that changed.
func editTask(tx *sql.Tx, tid int64, by string, home string, owner, reporter, title, body *string, priority, timeoutS *int64) (string, string, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return "", "", err
	}
	if t == nil {
		return fmt.Sprintf("no task T%d", tid), "", nil
	}
	// Anyone may change who works a task (owner) and who answers for it (reporter); every change is kept as an edit_task event.
	reshape := title != nil || priority != nil || timeoutS != nil || body != nil
	if reshape {
		if g := editGuard(guardRow(t), by); g != "" {
			return g, "", nil
		}
	}
	if owner != nil && *owner != t.Owner && *owner != human {
		if why, err := undeliverable(tx, t, *owner, home); err != nil || why != "" {
			return why, "", err
		}
	}
	if owner == nil && reporter == nil && !reshape {
		return "edit needs a new assignee, a new owner, a new title, a new priority, --timeout, a new body, or any of them", "", nil
	}
	var changes []change
	var newBody *string
	var bodyRID int64
	if body != nil {
		text := strings.TrimSpace(textsafe.Text(*body))
		if text == "" {
			return "a task needs a body: the work itself, what done looks like", "", nil
		}
		var rid int64
		var requester, old string
		err := tx.QueryRow("SELECT r.id, r.requester, COALESCE(r.body, '') FROM task_requests l JOIN requests r ON r.id = l.request_id "+
			"WHERE l.task_id = ? ORDER BY l.rowid LIMIT 1", tid).Scan(&rid, &requester, &old)
		if err != nil && err != sql.ErrNoRows {
			return "", "", err
		}
		if err == sql.ErrNoRows || requester != t.OpenedBy {
			return fmt.Sprintf("task T%d has no brief its opener wrote to edit - a task begun from a message, or linked to someone else's request, keeps those words as they are", tid), "", nil
		}
		if text != strings.TrimSpace(old) {
			newBody, bodyRID = &text, rid
			changes = append(changes, change{key: "body", old: old, new: text, body: true})
		}
	}
	var newOwner *string
	if owner != nil && *owner != t.Owner {
		if !contains(paths.ListAgents(home), *owner) {
			return "no agent named " + *owner, "", nil
		}
		newOwner = owner
		changes = append(changes, change{key: "owner", old: t.Owner, new: *owner})
	}
	var newReporter *string
	if reporter != nil && *reporter != t.reporter() {
		if *reporter != human && !contains(paths.ListAgents(home), *reporter) {
			return "no agent named " + *reporter, "", nil
		}
		newReporter = reporter
		changes = append(changes, change{key: "reporter", old: t.reporter(), new: *reporter})
	}
	var newTitle *string
	if title != nil {
		clean := strings.TrimSpace(textsafe.Oneline(*title))
		if clean == "" {
			return "a task needs a title", "", nil
		}
		if clean != t.Title {
			newTitle = &clean
			changes = append(changes, change{key: "title", old: t.Title, new: clean})
		}
	}
	var newPriority *int64
	if priority != nil {
		if *priority < 1 || *priority > 3 {
			return fmt.Sprintf("%d is not a priority - P1, P2 or P3", *priority), "", nil
		}
		var cur int64
		if err := tx.QueryRow("SELECT priority FROM tasks WHERE id = ?", tid).Scan(&cur); err != nil {
			return "", "", err
		}
		if *priority != cur {
			newPriority = priority
			changes = append(changes, change{key: "priority", old: priorityRef(cur), new: priorityRef(*priority)})
		}
	}
	doing := false
	if timeoutS != nil {
		switch {
		case *timeoutS <= 0:
			return "a task timeout is at least 1s - it counts time still to run, not none", "", nil
		case *timeoutS > db.MaxTaskTimeoutS:
			return fmt.Sprintf("a task timeout is at most %s - %s is longer", timefmt.Span(db.MaxTaskTimeoutS, false), timefmt.Span(*timeoutS, false)), "", nil
		}
		if doing, err = isDoing(tx, t); err != nil {
			return "", "", err
		}
		was := "none"
		if t.TimeoutS.Valid && t.TimeoutS.Int64 != 0 {
			was = timefmt.Span(t.TimeoutS.Int64, false)
		}
		now := timefmt.Span(*timeoutS, false) + ", once it is doing"
		if doing {
			now = timefmt.Span(*timeoutS, false) + ", from now"
		}
		changes = append(changes, change{key: "timeout", old: was, new: now})
	}
	if len(changes) == 0 {
		return fmt.Sprintf("task T%d already reads that way - nothing to edit", tid), "", nil
	}
	if err := db.EditTask(tx, tid, by, db.TaskEdit{Owner: newOwner, Reporter: newReporter, Title: newTitle, Priority: newPriority, TimeoutS: timeoutS, IsDoing: doing}); err != nil {
		return "", "", err
	}
	if newOwner != nil {
		went, err := db.SyncSelfBlocks(tx, tid, t.Owner)
		if err != nil {
			return "", "", err
		}
		for _, w := range went {
			if err := addEvent(tx, by, "dormant_block", kv{"task_id", w[0]}, kv{"blocks_on", w[1]}, kv{"why", "one agent holds both"}); err != nil {
				return "", "", err
			}
		}
	}
	if newBody != nil {
		if _, err := tx.Exec("UPDATE requests SET body = ? WHERE id = ?", *newBody, bodyRID); err != nil {
			return "", "", err
		}
	}
	payload := []kv{{"task_id", tid}}
	var said []string
	for _, c := range changes {
		payload = append(payload, kv{c.key, rpc.Pairs{{K: "old", V: c.old}, {K: "new", V: c.new}}})
		if c.body {
			said = append(said, "body changed")
		} else {
			said = append(said, fmt.Sprintf("%s %s -> %s", saidAs(c.key), pyRepr(c.old), pyRepr(c.new)))
		}
	}
	if err := addEvent(tx, by, "edit_task", payload...); err != nil {
		return "", "", err
	}
	return "", fmt.Sprintf("task T%d edited by %s: %s", tid, by, strings.Join(said, ", ")), nil
}

// saidAs is how an edit's reply names a field: the owner column holds who works the task (its assignee), the reporter who answers for it
// (its owner). The event keeps the column names.
func saidAs(key string) string {
	switch key {
	case "owner":
		return "assignee"
	case "reporter":
		return "owner"
	}
	return key
}

// editMethod is task.edit (rpc_tasks._edit): every part in one transaction, so a later refusal undoes the earlier parts.
func editMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		if err := unserved(p, "relation", "response", "reply", "waits_on"); err != nil {
			return nil, err
		}
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		optText := func(key string) (*string, error) {
			if p[key] == nil {
				return nil, nil
			}
			s, ok := p[key].(string)
			if !ok {
				return nil, badParam(key+" is text", key)
			}
			return &s, nil
		}
		var owner, reporter, title, body *string
		for _, f := range []struct {
			key string
			dst **string
		}{{"owner", &owner}, {"reporter", &reporter}, {"title", &title}, {"body", &body}} {
			if *f.dst, err = optText(f.key); err != nil {
				return nil, err
			}
		}
		secs, err := timeBox(p, "time_box")
		if err != nil {
			return nil, err
		}
		var timeoutS, priority *int64
		if secs != 0 {
			timeoutS = &secs
		}
		if p["priority"] != nil {
			n, ok := rpc.AsInt(p["priority"])
			if !ok || n < 1 || n > 3 {
				return nil, badParam("priority is 1, 2 or 3", "priority")
			}
			priority = &n
		}
		blocker, hasBlocker, err := optionalTask(p, "blocked_by")
		if err != nil {
			return nil, err
		}
		unblockOn, hasUnblock, err := optionalTask(p, "unblock")
		if err != nil {
			return nil, err
		}
		fields := owner != nil || reporter != nil || title != nil || body != nil || timeoutS != nil || priority != nil
		if !fields && !hasBlocker && !hasUnblock {
			return nil, badParam("edit needs an owner, a reporter, a title, a priority, a body, a time_box, blocked_by or unblock", "")
		}
		for _, who := range []*string{owner, reporter} {
			if who != nil && *who != human && !contains(paths.ListAgents(home), *who) {
				return nil, agentNotFound(*who)
			}
		}
		me := c.Caller.Actor()
		var done Edited
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			done = Edited{Task: tid, By: me}
			if t, err := getTask(tx, tid); err != nil {
				return nil, err
			} else if t != nil {
				done.WasOwner = t.Owner
			}
			var msgs []string
			if fields {
				why, msg, err := editTask(tx, tid, me, home, owner, reporter, title, body, priority, timeoutS)
				if err != nil {
					return nil, err
				}
				if why != "" {
					return nil, refusal(why)
				}
				msgs = append(msgs, msg)
			}
			if hasBlocker {
				why, err := recordBlock(tx, tid, blocker, me)
				if err != nil {
					return nil, err
				}
				if why != "" {
					return nil, refusal(why)
				}
				msgs = append(msgs, fmt.Sprintf("task T%d now waits on T%d", tid, blocker))
			}
			if hasUnblock {
				why, err := unblock(tx, tid, unblockOn, me)
				if err != nil {
					return nil, err
				}
				if why != "" {
					return nil, refusal(why)
				}
				msgs = append(msgs, fmt.Sprintf("task T%d no longer waits on T%d", tid, unblockOn))
				left, err := taskBlockers(tx, tid)
				if err != nil {
					return nil, err
				}
				if len(left) == 0 {
					t, err := getTask(tx, tid)
					if err != nil {
						return nil, err
					}
					done.Freed = t.Owner
				}
			}
			done.Body = body != nil
			if owner == nil {
				done.WasOwner = ""
			}
			return accepted(strings.Join(msgs, "; "), nil), nil
		})
		if err == nil && tell != nil {
			tell.Edited(done)
		}
		return out, err
	}
}
