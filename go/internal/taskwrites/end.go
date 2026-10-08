package taskwrites

import (
	"database/sql"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
)

// What a task's end does in the tables (handline/actions/tasks.py: _end_task, _clear_blocks_on, _notify_unblocked,
// _child_result_notice; handline/db/tasks.py and requests.py under them). The says it writes are rows; typing them at their
// target is the Teller's, once the write has committed.

const noticeFrom = "task-board"

// told is a say written for its target, to be delivered after the commit.
type told = db.Notice

// linked is a request linked to a task, as it is now.
type linked struct {
	ID                             int64
	Requester, Target, State, Kind string
	LandedAt, DeliveredAt          sql.NullInt64
	QueueOrder                     sql.NullFloat64
}

// asks is whether the request is work still being asked for (_asks): a say born landed never counts.
func (r linked) asks() bool {
	return r.State == "queued" || r.State == "landed" && r.Kind == "task" && r.QueueOrder.Valid
}

func taskRequests(tx *sql.Tx, tid int64) ([]linked, error) {
	rows, err := tx.Query("SELECT r.id, r.requester, r.target, r.state, r.kind, r.landed_at, r.delivered_at, r.queue_order "+
		"FROM requests r JOIN task_requests l ON l.request_id = r.id WHERE l.task_id = ? ORDER BY r.id", tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []linked
	for rows.Next() {
		var r linked
		if err := rows.Scan(&r.ID, &r.Requester, &r.Target, &r.State, &r.Kind, &r.LandedAt, &r.DeliveredAt, &r.QueueOrder); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// ids reads the first column of a query as numbers.
func ids(tx *sql.Tx, q string, args ...any) ([]int64, error) {
	rows, err := tx.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

const openSQL = "done_evidence IS NULL AND discarded_by IS NULL"

func openChildren(tx *sql.Tx, tid int64) ([]int64, error) {
	return ids(tx, "SELECT id FROM tasks WHERE parent_task = ? AND "+openSQL+" ORDER BY priority, id", tid)
}

// blockers are the open tasks still blocking tid (task_blockers): a done or discarded blocker no longer blocks.
type blocker struct {
	ID     int64
	Parent sql.NullInt64
}

func taskBlockers(tx *sql.Tx, tid int64) ([]blocker, error) {
	rows, err := tx.Query("SELECT t.id, t.parent_task FROM tasks t JOIN task_blocks b ON b.blocks_on = t.id "+
		"WHERE b.task_id = ? AND b.dormant = 0 AND t.discarded_by IS NULL AND t.done_evidence IS NULL ORDER BY t.id", tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []blocker
	for rows.Next() {
		var b blocker
		if err := rows.Scan(&b.ID, &b.Parent); err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, rows.Err()
}

func addBlock(tx *sql.Tx, tid, on int64) error {
	_, err := tx.Exec("INSERT OR IGNORE INTO task_blocks(task_id, blocks_on, created_at) VALUES (?, ?, ?)", tid, on, time.Now().Unix())
	return err
}

func removeBlock(tx *sql.Tx, tid, on int64) error {
	_, err := tx.Exec("DELETE FROM task_blocks WHERE task_id = ? AND blocks_on = ?", tid, on)
	return err
}

// reparentChildren moves tid's open children up to its own parent (or the top), and the wait tid holds on each with them.
func reparentChildren(tx *sql.Tx, tid int64) error {
	var parent sql.NullInt64
	if err := tx.QueryRow("SELECT parent_task FROM tasks WHERE id = ?", tid).Scan(&parent); err != nil {
		return err
	}
	kids, err := openChildren(tx, tid)
	if err != nil {
		return err
	}
	for _, k := range kids {
		if _, err := tx.Exec("UPDATE tasks SET parent_task = ? WHERE id = ?", parent, k); err != nil {
			return err
		}
		if err := removeBlock(tx, tid, k); err != nil {
			return err
		}
		if parent.Valid {
			if err := addBlock(tx, parent.Int64, k); err != nil {
				return err
			}
		}
	}
	return nil
}

// linkRequest links a request to a task and refreshes whether the task reads as sent.
func linkRequest(tx *sql.Tx, tid, rid int64) error {
	if _, err := tx.Exec("INSERT OR IGNORE INTO task_requests(task_id, request_id) VALUES (?, ?)", tid, rid); err != nil {
		return err
	}
	return refreshSent(tx, tid)
}

// addSay is db.add_request for a fire-and-forget say from `from` to `to` that starts its own chain. A notice, or any say to the
// founder, is born landed: nothing is owed on it and nothing wakes its target.
func addSay(tx *sql.Tx, from, to, body string, notice bool) (int64, error) {
	now := time.Now().Unix()
	landed := notice || to == human
	state := "queued"
	var landedAt, noticeCol any
	if landed {
		state, landedAt = "landed", now
	}
	if notice {
		noticeCol = 1
	}
	res, err := tx.Exec("INSERT INTO requests(ts, requester, target, body, files, relation, response, kind, chain_depth, parent_id, "+
		"state, chain_id, edge_ok, landed_at, state_at, notice) VALUES(?,?,?,?,NULL,NULL,'fire_and_forget','say',0,NULL,?,NULL,1,?,?,?)",
		now, from, to, body, state, landedAt, now, noticeCol)
	if err != nil {
		return 0, err
	}
	rid, _ := res.LastInsertId()
	_, err = tx.Exec("UPDATE requests SET chain_id = ?, queue_order = ? WHERE id = ?", strconv.FormatInt(rid, 10), rid, rid)
	return rid, err
}

// closeQueuedQuietly is db.close_if_queued(rid, state, None, announced=True, settle=False): the request leaves the queue because its
// task ended; a landed one closes as read, with an empty answer already told.
func closeQueuedQuietly(tx *sql.Tx, r linked) (closed bool, err error) {
	state := "withdrawn"
	if r.LandedAt.Valid {
		state = "landed"
	}
	now := time.Now().Unix()
	res, err := tx.Exec("UPDATE requests SET state = ?, state_at = ?, queue_order = NULL WHERE id = ? AND state = 'queued'", state, now, r.ID)
	if err != nil {
		return false, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return false, nil
	}
	if state == "landed" && r.Kind != "say" { // a closed task delivery tells its asker with a say of its own
		var requester, target, chain string
		var depth int64
		var chainID sql.NullString
		if err := tx.QueryRow("SELECT requester, target, chain_depth, chain_id FROM requests WHERE id = ?", r.ID).Scan(&requester, &target, &depth, &chainID); err != nil {
			return false, err
		}
		chain = chainID.String
		a, err := tx.Exec("INSERT INTO requests(ts, requester, target, body, kind, chain_depth, parent_id, settles, chain_id, state, delivered_at, state_at, edge_ok) "+
			"VALUES(?,?,?,?,'say',?,?,?,?,'landed',?,?,1)", now, target, requester, "", depth, r.ID, r.ID, chain, now, now)
		if err != nil {
			return false, err
		}
		aid, _ := a.LastInsertId()
		if _, err := tx.Exec("UPDATE requests SET queue_order = ? WHERE id = ?", aid, aid); err != nil {
			return false, err
		}
	}
	var tids []int64
	if tids, err = ids(tx, "SELECT task_id FROM task_requests WHERE request_id = ?", r.ID); err != nil {
		return false, err
	}
	for _, t := range tids {
		if err := refreshSent(tx, t); err != nil {
			return false, err
		}
	}
	return true, nil
}

var urgentWord = regexp.MustCompile(`(?i)^(fail(?:ed|ure)?|blocked|fatal)`)

// urgent is whether a task's closing words say it failed or is blocked (urgent_result).
func urgent(result string) bool {
	s := strings.TrimSpace(result)
	m := urgentWord.FindString(s)
	if m == "" {
		return false
	}
	rest := []rune(s[len(m):])
	return len(rest) == 0 || !(unicode.IsLetter(rest[0]) || unicode.IsDigit(rest[0]) || rest[0] == '_')
}

// endTask is _end_task: the task's queued deliveries close quietly, and whoever opened it, whoever asked through a linked delivery, and
// `also` are told `text` in one say each, written in this transaction. The opener's say is typed at once; the others' are notices for the
// digest unless the result is urgent (a done task that says it failed).
func endTask(tx *sql.Tx, home string, tid int64, by string, t *task, text string, urgentResult bool, also []string) ([]told, []db.InboxFile, error) {
	reqs, err := taskRequests(tx, tid)
	if err != nil {
		return nil, nil, err
	}
	var forget []db.InboxFile
	for _, r := range reqs {
		if r.State == "queued" {
			closed, err := closeQueuedQuietly(tx, r)
			if err != nil {
				return nil, nil, err
			}
			if closed {
				forget = append(forget, db.InboxFile{Target: r.Target, RID: r.ID})
			}
		}
	}
	skip := map[string]bool{by: true, noticeFrom: true}
	if t.Parent.Valid {
		var owner string
		switch err := tx.QueryRow("SELECT owner FROM tasks WHERE id = ?", t.Parent.Int64).Scan(&owner); {
		case err == nil:
			skip[owner] = true
		case err != sql.ErrNoRows:
			return nil, nil, err
		}
	}
	known := map[string]bool{human: true}
	for _, a := range paths.ListAgents(home) {
		known[a] = true
	}
	who := []string{t.OpenedBy}
	for _, r := range reqs {
		if r.Kind == "task" {
			who = append(who, r.Requester)
		}
	}
	who = append(who, also...)
	var out []told
	seen := map[string]bool{}
	for _, to := range who {
		if seen[to] {
			continue
		}
		seen[to] = true
		if skip[to] || !known[to] {
			continue
		}
		rid, err := addSay(tx, noticeFrom, to, text, to != t.OpenedBy && !urgentResult)
		if err != nil {
			return nil, nil, err
		}
		if err := addEvent(tx, by, "task_end_told", kv{"task_id", tid}, kv{"request_id", rid}, kv{"to", to}); err != nil {
			return nil, nil, err
		}
		out = append(out, told{RID: rid, To: to, Text: text})
	}
	return out, forget, nil
}

// clearBlocksOn drops every block row naming tid after it was discarded, and for each task left with no open blocker writes the
// notice for its owner (none for the closer's own task) (_clear_blocks_on).
func clearBlocksOn(tx *sql.Tx, tid int64, closer, how string) ([]told, []string, error) {
	deps, err := ids(tx, "SELECT task_id FROM task_blocks WHERE blocks_on = ? ORDER BY task_id", tid)
	if err != nil {
		return nil, nil, err
	}
	var out []told
	var freed []string
	seen := map[string]bool{}
	for _, dep := range deps {
		if err := removeBlock(tx, dep, tid); err != nil {
			return nil, nil, err
		}
		d, err := getTask(tx, dep)
		if err != nil {
			return nil, nil, err
		}
		if d == nil || d.done() || d.DiscardedBy.Valid && d.DiscardedBy.String != "" {
			continue
		}
		if n, err := childResultNotice(tx, d, closer, tid); err != nil {
			return nil, nil, err
		} else if n != nil {
			out = append(out, *n)
		}
		left, err := taskBlockers(tx, dep)
		if err != nil {
			return nil, nil, err
		}
		if len(left) > 0 {
			continue
		}
		if !seen[d.Owner] { // every owner left with a task to deliver, told or not (its queued task is not reconsidered on its own once skipped over blocked)
			seen[d.Owner] = true
			freed = append(freed, d.Owner)
		}
		if d.Owner == closer { // no notice for the closer, but the task still comes due again
			after, err := newestRequest(tx)
			if err != nil {
				return nil, nil, err
			}
			if err := addEvent(tx, closer, "unblock_task", kv{"task_id", dep}, kv{"blocks_on", tid}, kv{"after_request", after}); err != nil {
				return nil, nil, err
			}
			continue
		}
		n, err := notifyUnblocked(tx, dep, d.Owner, closer, tid, how)
		if err != nil {
			return nil, nil, err
		}
		out = append(out, n)
	}
	return out, freed, nil
}

func notifyUnblocked(tx *sql.Tx, dep int64, owner, closer string, closed int64, how string) (told, error) {
	text := fmt.Sprintf("task T%d is unblocked - T%d, its last open blocker, %s", dep, closed, how)
	var evidence sql.NullString
	var parent sql.NullInt64
	if err := tx.QueryRow("SELECT done_evidence, parent_task FROM tasks WHERE id = ?", closed).Scan(&evidence, &parent); err != nil {
		return told{}, err
	}
	if evidence.String != "" && !(parent.Valid && parent.Int64 == dep) { // a child's result is told on its own
		text += ": " + evidence.String
	}
	rid, err := addSay(tx, noticeFrom, owner, text, false)
	if err != nil {
		return told{}, err
	}
	if err := linkRequest(tx, dep, rid); err != nil {
		return told{}, err
	}
	after, err := newestRequest(tx)
	if err != nil {
		return told{}, err
	}
	if err := addEvent(tx, closer, "task_unblocked", kv{"task_id", dep}, kv{"request_id", rid}, kv{"closed_task", closed}, kv{"after_request", after}); err != nil {
		return told{}, err
	}
	return told{RID: rid, To: owner, Text: text}, nil
}

// childResultNotice tells the owner of the parent how a child ended, once per finish (a reopen voids it).
func childResultNotice(tx *sql.Tx, dep *task, by string, child int64) (*told, error) {
	var parent sql.NullInt64
	var evidence, discardedBy, reason sql.NullString
	var owner string
	if err := tx.QueryRow("SELECT parent_task, done_evidence, discarded_by, discard_reason, owner FROM tasks WHERE id = ?", child).
		Scan(&parent, &evidence, &discardedBy, &reason, &owner); err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}
	if !parent.Valid || parent.Int64 != dep.ID || dep.Owner == by {
		return nil, nil
	}
	var since int64
	if err := tx.QueryRow("SELECT COALESCE(MAX(id), 0) FROM events WHERE kind = 'reopen_task' AND "+eventTask+" = ?", child).Scan(&since); err != nil {
		return nil, err
	}
	var n int
	if err := tx.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'child_result_told' AND "+eventTask+" = ? AND json_extract(payload, '$.child') = ? AND id > ?",
		dep.ID, child, since).Scan(&n); err != nil {
		return nil, err
	}
	if n > 0 {
		return nil, nil
	}
	var text string
	switch {
	case evidence.String != "":
		text = fmt.Sprintf("T%d done by %s: %s", child, owner, strings.Join(strings.Fields(evidence.String), " "))
	case discardedBy.String != "":
		text = fmt.Sprintf("T%d discarded by %s: %s", child, discardedBy.String, strings.Join(strings.Fields(reason.String), " "))
	default:
		return nil, nil
	}
	text = strings.TrimRight(text, ": ")
	rid, err := addSay(tx, noticeFrom, dep.Owner, text, !urgent(evidence.String))
	if err != nil {
		return nil, err
	}
	if err := linkRequest(tx, dep.ID, rid); err != nil {
		return nil, err
	}
	if err := addEvent(tx, by, "child_result_told", kv{"task_id", dep.ID}, kv{"child", child}, kv{"request_id", rid}); err != nil {
		return nil, err
	}
	return &told{RID: rid, To: dep.Owner, Text: text}, nil
}
