package reads

import (
	"fmt"
	"slices"
	"sort"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// The founder's requests: the tasks they opened (a message made a request, or a task of their own for a lead) and the messages they sent that
// are not a task yet. Each request's status is read from its tree of tasks and stored nowhere.

// Request statuses, in the order the first that holds is the status.
const (
	reqWaitingOnYou = "waiting_on_you" // a task assigned to the founder, or a decision card, anywhere in the tree
	reqReview       = "review"         // an open task has a PR
	reqDoing        = "doing"          // some task in the tree is open
	reqDone         = "done"           // the request's own task is done
	reqNoWork       = "no_work"        // a message marked as needing no work
	reqNotYet       = "not_yet"        // a message with neither a task nor a no-work mark
)

const (
	// requestWindowS is how far back a message, or a request done, is listed.
	requestWindowS = 30 * 24 * 3600
	// tellEvent is the event noteRequestComment (taskwrites) keeps the count of unread agent comments in.
	tellEvent = "request_comment_told"
)

// requestNode is one task of a request's tree.
type requestNode struct {
	id, at, pr int64
	owner      string
	open       bool
}

// RequestRows is requests.list's rows, newest activity first, before filtering by state and paging.
func (e *Env) RequestRows(now int64) ([]map[string]any, error) {
	since := now - requestWindowS
	roots, err := query(e.DB, "SELECT id, title, owner, from_request, created_at, done_evidence, done_at FROM tasks WHERE opened_by = ? AND owner != ? "+
		"AND parent_task IS NULL AND discarded_by IS NULL AND (done_evidence IS NULL OR done_at >= ?) ORDER BY id", human, human, since)
	if err != nil {
		return nil, err
	}
	nodes, err := e.requestNodes(since)
	if err != nil {
		return nil, err
	}
	cards, err := e.cardTasks()
	if err != nil {
		return nil, err
	}
	unread, err := e.unreadComments()
	if err != nil {
		return nil, err
	}
	origins, err := e.originAgents()
	if err != nil {
		return nil, err
	}
	var out []map[string]any
	for _, r := range roots {
		id := r.i("id")
		tree := nodes[id]
		status, holders, prs := reqDoing, map[string]bool{}, map[int64]bool{}
		open, done, last, waiting, review := 0, 0, r.i("created_at"), false, false
		for _, n := range tree {
			last = max(last, n.at)
			if n.id != id {
				if n.open {
					open++
				} else {
					done++
				}
			}
			if !n.open {
				continue
			}
			if n.owner == human || cards[n.id] {
				waiting = true
			} else if n.owner != db.Gate {
				holders[n.owner] = true
			}
			if n.pr != 0 {
				review, prs[n.pr] = true, true
			}
		}
		switch {
		case r.set("done_evidence"):
			status, holders = reqDone, map[string]bool{}
		case waiting:
			status = reqWaitingOnYou
		case review:
			status = reqReview
		}
		agent := r.str("owner")
		row := map[string]any{"kind": "task", "task": id, "ref": nil, "title": r.str("title"), "status": status, "agent": agent,
			"holders": sortedKeys(holders), "open": open, "done": done, "prs": sortedNums(prs), "reason": nil, "unread_comments": unread[id],
			"created": ts(r["created_at"]), "last_activity": ts(last), "cursor": cursorOf(last, "t", id)}
		if rid := r.i("from_request"); rid != 0 {
			row["ref"] = origins[rid].ref
			row["origin"] = origins[rid].agent
		}
		out = append(out, row)
	}
	msgs, err := query(e.DB, "SELECT id, ref, ts, target, body, no_work FROM requests r WHERE requester = ? AND target != ? AND parent_id IS NULL "+
		"AND state NOT IN ('denied', 'withdrawn') AND ts >= ? "+
		"AND NOT EXISTS (SELECT 1 FROM task_requests l WHERE l.request_id = r.id) AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.from_request = r.id) "+
		"ORDER BY id", human, human, since)
	if err != nil {
		return nil, err
	}
	for _, m := range msgs {
		status, reason := reqNotYet, any(nil)
		if m.set("no_work") {
			status, reason = reqNoWork, m.str("no_work")
		}
		out = append(out, map[string]any{"kind": "message", "task": nil, "ref": db.MsgPrefix + m.str("ref"), "title": head(firstLine(m.str("body")), 80),
			"status": status, "agent": m.str("target"), "holders": []string{}, "open": 0, "done": 0, "prs": []int64{}, "reason": reason, "unread_comments": 0,
			"created": ts(m["ts"]), "last_activity": ts(m["ts"]), "cursor": cursorOf(m.i("ts"), "m", m.i("id"))})
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i]["cursor"].(string) > out[j]["cursor"].(string) })
	return out, nil
}

// cursorOf orders rows by last activity, then kind and number; text of one width sorts as it reads.
func cursorOf(last int64, kind string, id int64) string {
	return fmt.Sprintf("%012d:%s%012d", last, kind, id)
}

// requestNodes is the tasks of every request's tree, by the root's number: the root and every task under it, kept or ended since.
func (e *Env) requestNodes(since int64) (map[int64][]requestNode, error) {
	rows, err := query(e.DB, "WITH RECURSIVE sub(root, id) AS (SELECT id, id FROM tasks WHERE opened_by = ? AND owner != ? AND parent_task IS NULL "+
		"UNION SELECT s.root, k.id FROM tasks k JOIN sub s ON k.parent_task = s.id) "+
		"SELECT s.root, t.id, t.owner, COALESCE(t.pr, 0) AS pr, (t.done_evidence IS NULL AND t.discarded_by IS NULL) AS open, "+
		"MAX(t.created_at, COALESCE(t.done_at, 0), COALESCE(t.edited_at, 0), COALESCE(t.discarded_at, 0), "+
		"COALESCE((SELECT MAX(m.created_at) FROM task_memos m WHERE m.task_id = t.id), 0)) AS at "+
		"FROM sub s JOIN tasks t ON t.id = s.id WHERE t.discarded_by IS NULL OR t.discarded_at >= ? ORDER BY t.id", human, human, since)
	if err != nil {
		return nil, err
	}
	out := map[int64][]requestNode{}
	for _, r := range rows {
		out[r.i("root")] = append(out[r.i("root")], requestNode{id: r.i("id"), owner: r.str("owner"), pr: r.i("pr"), open: r.i("open") == 1, at: r.i("at")})
	}
	return out, nil
}

// cardTasks is the tasks with a decision card waiting on the founder: a queued request to them.
func (e *Env) cardTasks() (map[int64]bool, error) {
	rows, err := query(e.DB, "SELECT DISTINCT l.task_id FROM task_requests l JOIN requests r ON r.id = l.request_id WHERE r.target = ? AND r.state = 'queued'", human)
	if err != nil {
		return nil, err
	}
	out := map[int64]bool{}
	for _, r := range rows {
		out[r.i("task_id")] = true
	}
	return out, nil
}

// unreadComments is, by request task, the agents' new comments the founder has not read: what the newest inbox line for it counts.
func (e *Env) unreadComments() (map[int64]int64, error) {
	rows, err := query(e.DB, "SELECT json_extract(e.payload, '$.task_id') AS task, json_extract(e.payload, '$.count') AS n, r.read_at AS read_at FROM events e "+
		"JOIN requests r ON r.id = CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.request_id') END WHERE e.kind = ? ORDER BY e.id", tellEvent)
	if err != nil {
		return nil, err
	}
	out := map[int64]int64{}
	for _, r := range rows {
		if r.set("read_at") {
			out[r.i("task")] = 0
		} else {
			out[r.i("task")] = r.i("n")
		}
	}
	return out, nil
}

type origin struct{ ref, agent string }

// originAgents is, by message, the ref and the agent it was sent to, for the requests made from a message.
func (e *Env) originAgents() (map[int64]origin, error) {
	rows, err := query(e.DB, "SELECT id, ref, target FROM requests WHERE id IN (SELECT from_request FROM tasks WHERE from_request IS NOT NULL)")
	if err != nil {
		return nil, err
	}
	out := map[int64]origin{}
	for _, r := range rows {
		out[r.i("id")] = origin{ref: db.MsgPrefix + r.str("ref"), agent: r.str("target")}
	}
	return out, nil
}

func sortedKeys(m map[string]bool) []string {
	out := []string{}
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedNums(m map[int64]bool) []int64 {
	out := []int64{}
	for k := range m {
		out = append(out, k)
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}

// requestsList is requests.list {state?, before?, limit?}: the rows of RequestRows with the status asked for, a page after the cursor.
func (e *Env) requestsList(_ *rpc.Conn, params map[string]any) (any, error) {
	state, err := optStr(params, "state")
	if err != nil {
		return nil, err
	}
	if state != nil && !slices.Contains([]string{reqWaitingOnYou, reqReview, reqDoing, reqDone, reqNoWork, reqNotYet}, *state) {
		return nil, badParam("state is waiting_on_you, review, doing, done, no_work or not_yet", "state")
	}
	before, err := optStr(params, "before")
	if err != nil {
		return nil, err
	}
	limit, err := pageLimit(params)
	if err != nil {
		return nil, err
	}
	rows, err := e.RequestRows(time.Now().Unix())
	if err != nil {
		return nil, err
	}
	var kept []any
	more := false
	for _, r := range rows {
		if (state != nil && r["status"] != *state) || (before != nil && r["cursor"].(string) >= *before) {
			continue
		}
		if len(kept) == limit {
			more = true
			break
		}
		kept = append(kept, r)
	}
	if kept == nil {
		kept = []any{}
	}
	return map[string]any{"items": kept, "more": more}, nil
}
