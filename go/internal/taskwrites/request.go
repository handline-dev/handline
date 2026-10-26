package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/requestref"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// titleMax is the longest title task.request takes from a message's first line.
const titleMax = 80

// founderMessage is the founder's message to an agent that task.request and task.none act on: the one who may act on it is the founder or
// that agent. A message that is not so is refused, naming why.
func founderMessage(tx *sql.Tx, home string, c *rpc.Conn, p map[string]any) (int64, *db.Request, error) {
	me := c.Caller.Actor()
	rid, err := requestref.Resolve(tx, home, p["request"], me, "request")
	if name, missing := requestref.Missing(err); missing {
		return 0, nil, refusal("no request #" + name)
	}
	if err != nil {
		return 0, nil, err
	}
	req, err := db.GetRequest(tx, rid)
	if err != nil {
		return 0, nil, err
	}
	name := db.MsgName(tx, rid)
	switch {
	case req.Requester != human || req.Target == human:
		return 0, nil, refusal(fmt.Sprintf("%s is not a message from the founder to an agent", name))
	case me != human && me != req.Target:
		return 0, nil, refusal(fmt.Sprintf("%s was sent to %s: only it, or the founder, may act on it", name, req.Target))
	}
	return rid, req, nil
}

// requestTask is the task made from message rid: a request task (from_request), or one begun on the message; 0 for none.
func requestTask(tx *sql.Tx, rid int64) (tid int64, err error) {
	err = tx.QueryRow("SELECT COALESCE((SELECT id FROM tasks WHERE from_request = ? ORDER BY id LIMIT 1), "+
		"(SELECT task_id FROM task_requests WHERE request_id = ? ORDER BY task_id LIMIT 1), 0)", rid, rid).Scan(&tid)
	return tid, err
}

// requestMethod is task.request: a founder's message becomes a task the founder opened (opened_by human), held by the lead it was sent to,
// with the message as its first comment. A message converts once.
func requestMethod(home string) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		owner, title := "", ""
		if p["owner"] != nil {
			var err error
			if owner, err = text(p, "owner"); err != nil {
				return nil, err
			}
			if !contains(paths.ListAgents(home), owner) {
				return nil, agentNotFound(owner)
			}
		}
		if p["title"] != nil {
			var err error
			if title, err = text(p, "title"); err != nil {
				return nil, err
			}
		}
		return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			rid, req, err := founderMessage(tx, home, c, p)
			if err != nil {
				return nil, err
			}
			name := db.MsgName(tx, rid)
			if tid, err := requestTask(tx, rid); err != nil {
				return nil, err
			} else if tid != 0 {
				return accepted(fmt.Sprintf("%s is already T%d", name, tid), map[string]any{"task": tid, "existing": true}), nil
			}
			if owner == "" {
				owner = req.Target
			}
			clean := strings.TrimSpace(textsafe.Oneline(title))
			if clean == "" {
				clean = headline(req.Body)
			}
			tid, err := db.NewTask(tx, clean, owner, human, 0, db.DefaultPriority, "report", 0, "")
			if err != nil {
				return nil, err
			}
			if _, err := tx.Exec("UPDATE tasks SET from_request = ? WHERE id = ?", rid, tid); err != nil {
				return nil, err
			}
			if err := db.LinkRequest(tx, tid, rid); err != nil {
				return nil, err
			}
			if _, err := tx.Exec("UPDATE requests SET no_work = NULL, no_work_by = NULL, state_at = ? WHERE id = ?", db.Now(), rid); err != nil {
				return nil, err
			}
			if _, err := db.SetTaskQueued(tx, tid, human); err != nil { // the founder's own task needs no one's approval
				return nil, err
			}
			if _, held, err := db.CurrentTask(tx, owner); err != nil {
				return nil, err
			} else if !held {
				if err := db.SetCurrentTask(tx, owner, tid, true); err != nil {
					return nil, err
				}
			}
			mid, err := db.AddMemo(tx, tid, human, req.Body)
			if err != nil {
				return nil, err
			}
			files, err := attachments.Of(tx, rid)
			if err != nil {
				return nil, err
			}
			if err := attachments.New(home).AttachMemo(tx, mid, files); err != nil {
				return nil, err
			}
			if err := addEvent(tx, c.Caller.Actor(), "task_request", kv{"task_id", tid}, kv{"chat_request", rid}, kv{"owner", owner}); err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("%s is now request T%d, held by %s", name, tid, owner), map[string]any{"task": tid}), nil
		})
	}
}

// headline is a message's first line, cut to a title.
func headline(body string) string {
	line := strings.TrimSpace(textsafe.Oneline(strings.SplitN(strings.TrimSpace(body), "\n", 2)[0]))
	if r := []rune(line); len(r) > titleMax {
		line = strings.TrimSpace(string(r[:titleMax-1])) + "…"
	}
	if line == "" {
		return "(a message without words)"
	}
	return line
}

// noneMethod is task.none: a founder's message marked as needing no work, with the reason.
func noneMethod(home string) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		reason, err := text(p, "reason")
		if err != nil {
			return nil, err
		}
		reason = strings.TrimSpace(textsafe.Oneline(reason))
		return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			rid, _, err := founderMessage(tx, home, c, p)
			if err != nil {
				return nil, err
			}
			name := db.MsgName(tx, rid)
			if tid, err := requestTask(tx, rid); err != nil {
				return nil, err
			} else if tid != 0 {
				return nil, refusal(fmt.Sprintf("%s is already T%d: it is work", name, tid))
			}
			if _, err := tx.Exec("UPDATE requests SET no_work = ?, no_work_by = ?, state_at = ? WHERE id = ?", reason, c.Caller.Actor(), db.Now(), rid); err != nil {
				return nil, err
			}
			if err := addEvent(tx, c.Caller.Actor(), "request_no_work", kv{"chat_request", rid}, kv{"reason", reason}); err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("%s marked as needing no work: %s", name, reason), nil), nil
		})
	}
}

// noteRequestComment tells the founder, in their inbox, of an agent's comment on a request: one line per request while it stays unread, which
// counts the comments since. The founder's own comments, and comments on any other task, tell no one.
func noteRequestComment(tx *sql.Tx, tid int64, by string) error {
	if by == human {
		return nil
	}
	var title string
	err := tx.QueryRow("SELECT title FROM tasks WHERE id = ? AND from_request IS NOT NULL AND opened_by = ?", tid, human).Scan(&title)
	if err == sql.ErrNoRows {
		return nil
	} else if err != nil {
		return err
	}
	var rid, n int64
	err = tx.QueryRow("SELECT r.id, COALESCE(json_extract(e.payload, '$.count'), 0) FROM events e JOIN requests r ON "+
		"r.id = CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.request_id') END "+
		"WHERE e.kind = 'request_comment_told' AND CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.task_id') END = ? "+
		"AND r.read_at IS NULL ORDER BY e.id DESC LIMIT 1", tid).Scan(&rid, &n)
	if err != nil && err != sql.ErrNoRows {
		return err
	}
	n++
	body := fmt.Sprintf("Request T%d %q has %d new comment(s), the latest by %s: `hl task comment T%d list`", tid, title, n, by, tid)
	if err == sql.ErrNoRows {
		if rid, err = addSay(tx, noticeFrom, human, body, false); err != nil {
			return err
		}
		if _, err := tx.Exec("INSERT OR IGNORE INTO task_requests(task_id, request_id) VALUES(?, ?)", tid, rid); err != nil {
			return err
		}
	} else if _, err := tx.Exec("UPDATE requests SET body = ?, ts = ? WHERE id = ?", body, time.Now().Unix(), rid); err != nil {
		return err
	}
	return addEvent(tx, noticeFrom, "request_comment_told", kv{"task_id", tid}, kv{"request_id", rid}, kv{"count", n})
}
