package reads

import (
	"fmt"
	"github.com/handline/handline/go/internal/taskresult"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

const human = "human"

const eventTask = "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END"

// duration is the spec's Duration in the largest whole unit: 2h, 90m, 1d.
func duration(secs int64) string {
	for _, u := range []struct {
		name string
		size int64
	}{{"d", 86400}, {"h", 3600}, {"m", 60}} {
		if secs%u.size == 0 {
			return fmt.Sprintf("%d%s", secs/u.size, u.name)
		}
	}
	return fmt.Sprintf("%ds", secs)
}

func ended(t row) bool { return t.str("done_evidence") != "" || t.str("discarded_by") != "" }

// openTask is ended in SQL, negated: a task with no done evidence that was not discarded.
const openTask = "COALESCE(done_evidence, '') = '' AND COALESCE(discarded_by, '') = ''"

// board is what the state of a task needs, read in a handful of queries (_board_facts in actions/tasks.py): the
// tasks begun on a founder's message, the ones with a request out, each open task's open children, what every owner
// holds now.
type board struct {
	begun    map[int64]bool
	sent     map[int64]bool
	children map[int64][]int64
	current  map[string]int64
	held     map[int64]row // the tasks owners hold now, for whether each ended
}

func (e *Env) loadBoard() (*board, error) {
	b := &board{begun: map[int64]bool{}, sent: map[int64]bool{}, children: map[int64][]int64{},
		current: map[string]int64{}, held: map[int64]row{}}
	begunTask := "CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.task_id') END"
	rows, err := query(e.DB, "SELECT DISTINCT "+begunTask+" AS t FROM events e WHERE kind IN ('task_begin', 'task_self_start')"+db.SinceRelease(begunTask))
	if err != nil {
		return nil, err
	}
	for _, r := range rows {
		if r["t"] != nil {
			b.begun[r.i("t")] = true
		}
	}
	if rows, err = query(e.DB, "SELECT id FROM tasks WHERE sent_at IS NOT NULL"); err != nil {
		return nil, err
	}
	for _, r := range rows {
		b.sent[r.i("id")] = true
	}
	if rows, err = query(e.DB, "SELECT parent_task, id FROM tasks WHERE parent_task IS NOT NULL AND "+openTask+" ORDER BY priority, id"); err != nil {
		return nil, err
	}
	for _, r := range rows {
		b.children[r.i("parent_task")] = append(b.children[r.i("parent_task")], r.i("id"))
	}
	if rows, err = query(e.DB, "SELECT agent, current_task FROM agent_state WHERE current_task IS NOT NULL"); err != nil {
		return nil, err
	}
	for _, r := range rows {
		b.current[r.str("agent")] = r.i("current_task")
	}
	if rows, err = query(e.DB, "SELECT id, discarded_by, done_evidence, reported_at FROM tasks WHERE id IN "+
		"(SELECT current_task FROM agent_state WHERE current_task IS NOT NULL)"); err != nil {
		return nil, err
	}
	for _, r := range rows {
		b.held[r.i("id")] = r
	}
	return b, nil
}

// holds is whether the task is the one its owner holds: the founder holds none and every task of theirs reads as held.
func (b *board) holds(t row) bool {
	held, has := b.current[t.str("owner")]
	if t.str("owner") == human || !has || held == t.i("id") {
		return true
	}
	cur, ok := b.held[held]
	return !ok || ended(cur) || cur.set("reported_at")
}

func (b *board) asked(id int64) bool {
	if b.begun[id] || b.sent[id] {
		return true
	}
	for _, c := range b.children[id] {
		if b.sent[c] {
			return true
		}
	}
	return false
}

// state is one of draft, queued, doing, reported, done, discarded.
func (b *board) state(t row) string {
	if t.str("discarded_by") != "" {
		return "discarded"
	}
	if t.str("done_evidence") != "" {
		return "done"
	}
	if t.set("reported_at") {
		return "reported"
	}
	asked := b.asked(t.i("id"))
	if asked && b.holds(t) {
		return "doing"
	}
	if t.str("queued_by") != "" || asked {
		return "queued"
	}
	return "draft"
}

// openTasks are the open tasks in priority then number order.
func (e *Env) openTasks() ([]row, error) {
	return query(e.DB, "SELECT * FROM tasks WHERE "+openTask+" ORDER BY priority, id")
}

// reach is the agents whose tasks agent sees: itself and everyone under it, walked down the supervisor edges the
// stored views record (the Python daemon reads the same from team.yaml and writes them into agent_view each tick).
// Reach is the agent and the agents under it that are registered: what an agent session may read of tasks and notifications.
func (e *Env) Reach(agent string) (map[string]bool, error) { return e.reach(agent) }

func (e *Env) reach(agent string) (map[string]bool, error) {
	views, err := query(e.DB, "SELECT agent, view FROM agent_view")
	if err != nil {
		return nil, err
	}
	boss := map[string]string{}
	for _, v := range views {
		if p, _ := decodeObject(v.str("view"))["parent"].(string); p != "" {
			boss[v.str("agent")] = p
		}
	}
	registered := map[string]bool{}
	for _, n := range e.listAgents() {
		registered[n] = true
	}
	seen, todo := map[string]bool{agent: true}, []string{agent}
	for len(todo) > 0 {
		top := todo[len(todo)-1]
		todo = todo[:len(todo)-1]
		for a, b := range boss {
			if b == top && registered[a] && !seen[a] {
				seen[a] = true
				todo = append(todo, a)
			}
		}
	}
	return seen, nil
}

func (e *Env) tasksList(c *rpc.Conn, params map[string]any) (any, error) {
	owner, err := optStr(params, "owner")
	if err != nil {
		return nil, err
	}
	closer, err := optStr(params, "reporter") // who the task answers to: its reporter, else as askerOf
	if err != nil {
		return nil, err
	}
	opener, err := optStr(params, "from")
	if err != nil {
		return nil, err
	}
	state, err := optStr(params, "state")
	if err != nil {
		return nil, err
	}
	parent, err := optInt(params, "parent")
	if err != nil {
		return nil, err
	}
	under, err := optInt(params, "under")
	if err != nil {
		return nil, err
	}
	before, err := optInt(params, "before")
	if err != nil {
		return nil, err
	}
	limit, err := pageLimit(params)
	if err != nil {
		return nil, err
	}
	withEnded, err := optBool(params, "all")
	if err != nil {
		return nil, err
	}
	everyone, err := optBool(params, "everyone")
	if err != nil {
		return nil, err
	}
	tags, err := optTags(params, "tags")
	if err != nil {
		return nil, err
	}
	var owners map[string]bool
	if !everyone && c.Caller.Role == "agent" {
		if owners, err = e.reach(c.Caller.Info["name"].(string)); err != nil {
			return nil, err
		}
	}
	for _, name := range []*string{owner, closer} {
		if name != nil && owners != nil && !owners[*name] {
			return nil, rpc.Refused(fmt.Sprintf("%s is not you or one of your reports: everyone shows every team's tasks", *name), "", "")
		}
	}
	// The filter is SQL, so a board of thousands is not loaded to show a few. Only the state's last step (draft, queued or doing) is
	// the board's, read for the rows the SQL kept.
	where, args := []string{"1 = 1"}, []any{}
	cond := func(sql string, a ...any) {
		where = append(where, sql)
		args = append(args, a...)
	}
	if !(withEnded || under != nil || (state != nil && (*state == "done" || *state == "discarded"))) {
		cond(openTask)
	}
	if state != nil {
		switch *state {
		case "discarded":
			cond("COALESCE(discarded_by, '') != ''")
		case "done":
			cond("COALESCE(discarded_by, '') = '' AND COALESCE(done_evidence, '') != ''")
		case "reported":
			cond("reported_at IS NOT NULL AND " + openTask)
		default:
			cond("reported_at IS NULL")
		}
	}
	for _, f := range []struct {
		col string
		v   *string
	}{{"owner", owner}, {"opened_by", opener}, {askerSQL, closer}} {
		if f.v != nil {
			cond(f.col+" = ?", *f.v)
		}
	}
	if owners != nil { // a task is in scope by its assignee or its owner
		names := make([]any, 0, len(owners))
		for n := range owners {
			names = append(names, n)
		}
		in := "(" + placeholders(len(names)) + ")"
		cond("(owner IN "+in+" OR "+askerSQL+" IN "+in+")", append(names, names...)...)
	}
	if parent != nil {
		cond("parent_task = ?", *parent)
	}
	if under != nil {
		cond("id IN (WITH RECURSIVE story(id) AS (SELECT ? UNION SELECT k.id FROM tasks k JOIN story ON k.parent_task = story.id) SELECT id FROM story)", *under)
	}
	if before != nil {
		cond("id < ?", *before)
	}
	for _, tag := range tags {
		cond("EXISTS (SELECT 1 FROM task_tags g WHERE g.task_id = tasks.id AND g.tag = ?)", tag)
	}
	page := ""
	if state == nil || *state == "reported" || *state == "done" || *state == "discarded" { // SQL alone decides: it pages too
		page = fmt.Sprintf(" LIMIT %d", limit+1)
	}
	shown, err := query(e.DB, "SELECT * FROM tasks WHERE "+strings.Join(where, " AND ")+" ORDER BY id DESC"+page, args...)
	if err != nil {
		return nil, err
	}
	b, err := e.loadBoard()
	if err != nil {
		return nil, err
	}
	type item struct {
		t     row
		state string
	}
	var rows []item
	for _, t := range shown {
		if st := b.state(t); state == nil || st == *state {
			rows = append(rows, item{t, st})
		}
	}
	items := []any{}
	for i, r := range rows {
		if i >= limit {
			break
		}
		task, err := e.taskOf(r.t, r.state)
		if err != nil {
			return nil, err
		}
		items = append(items, task)
	}
	return map[string]any{"items": items, "more": len(rows) > limit}, nil
}

func (e *Env) firstRequest(tid int64) (row, error) {
	return queryOne(e.DB, "SELECT r.* FROM task_requests l JOIN requests r ON r.id = l.request_id "+
		"WHERE l.task_id = ? ORDER BY l.rowid LIMIT 1", tid)
}

// askerSQL is askerOf as a column of a tasks row. The + keeps the planner off requests_requester, which would read every request
// of the founder's for each task.
const askerSQL = "COALESCE(NULLIF(reporter, ''), CASE WHEN EXISTS (SELECT 1 FROM task_requests l JOIN requests r ON r.id = l.request_id " +
	"WHERE l.task_id = tasks.id AND +r.requester = 'human') THEN 'human' ELSE opened_by END)"

// askerOf is actions.task_asker: who a task answers to when it closes. Its reporter when one was set; else the founder, if any request
// linked to it came from the founder; else whoever opened it.
func (e *Env) askerOf(t row) (string, error) {
	if r := t.str("reporter"); r != "" {
		return r, nil
	}
	r, err := queryOne(e.DB, "SELECT 1 AS yes FROM task_requests l JOIN requests r ON r.id = l.request_id "+
		"WHERE l.task_id = ? AND +r.requester = ? LIMIT 1", t.i("id"), human) // + as in askerSQL
	if err != nil {
		return "", err
	}
	if r != nil {
		return human, nil
	}
	return t.str("opened_by"), nil
}

func (e *Env) blockers(tid int64) ([]row, error) {
	return query(e.DB, "SELECT t.* FROM tasks t JOIN task_blocks b ON b.blocks_on = t.id "+
		"WHERE b.task_id = ? AND b.dormant = 0 AND t.discarded_by IS NULL AND t.done_evidence IS NULL ORDER BY t.id", tid)
}

// taskOf is the spec's Task: the row as the board shows it, without its body.
func (e *Env) taskOf(t row, state string) (map[string]any, error) {
	first, err := e.firstRequest(t.i("id"))
	if err != nil {
		return nil, err
	}
	waits, err := query(e.DB, "SELECT b.blocks_on AS task, b.created_at, t.discarded_by IS NULL AND t.done_evidence IS NULL AS open "+
		"FROM task_blocks b JOIN tasks t ON t.id = b.blocks_on WHERE b.task_id = ? AND b.dormant = 0 ORDER BY b.created_at, b.blocks_on", t.i("id"))
	if err != nil {
		return nil, err
	}
	rec, err := e.holdRec(t.i("id"))
	if err != nil {
		return nil, err
	}
	hold := waitLine(rec, state)
	clock, err := e.taskClock(t, state)
	if err != nil {
		return nil, err
	}
	asker, err := e.askerOf(t)
	if err != nil {
		return nil, err
	}
	reqs, err := query(e.DB, "SELECT r.*, MAX(COALESCE(r.ts, 0), COALESCE(r.state_at, 0), "+
		"COALESCE((SELECT MAX(a.ts) FROM requests a WHERE a.settles = r.id), 0)) AS touched "+
		"FROM requests r JOIN task_requests l ON l.request_id = r.id WHERE l.task_id = ? ORDER BY r.id", t.i("id"))
	if err != nil {
		return nil, err
	}
	blockedBy, waitList := []int64{}, make([]any, len(waits))
	for i, w := range waits {
		waitList[i] = map[string]any{"task": w.i("task"), "open": w.i("open") == 1}
		if w.i("open") == 1 {
			blockedBy = append(blockedBy, w.i("task"))
		}
	}
	sort.Slice(blockedBy, func(i, j int) bool { return blockedBy[i] < blockedBy[j] })
	waiting, err := e.waitingOf(t, state, waits, rec)
	if err != nil {
		return nil, err
	}
	task := map[string]any{
		"blocked_by": blockedBy, "waits": waitList, "waiting": waiting, "time": clock,
		"asker": asker, "id": t.i("id"), "from": t.str("opened_by"), "to": t.str("owner"), "title": t.str("title"), "state": state,
		"relation": first.str("relation"), "response": first.str("response"), "time_box": nil,
		"parent": t.intOrNil("parent_task"), "created": ts(t["created_at"]), "delivered": ts(t["delivered_at"]),
		"done": ts(t["done_at"]), "priority": t.i("priority"), "hold_reason": hold, "block_reason": nil, "evidence": t.strOrNil("done_evidence"),
	}
	active := max(t.i("created_at"), t.i("edited_at"), t.i("done_at"), t.i("discarded_at"))
	linked := make([]map[string]any, len(reqs))
	for i, r := range reqs {
		active = max(active, r.i("touched"))
		linked[i] = map[string]any{"id": r.i("id"), "ref": r.str("ref"), "from": r.str("requester"), "to": r.str("target"),
			"state": r.str("state"), "at": ts(r["ts"])}
	}
	tagRows, err := query(e.DB, "SELECT tag FROM task_tags WHERE task_id = ? ORDER BY tag", t.i("id"))
	if err != nil {
		return nil, err
	}
	tags := make([]string, len(tagRows))
	for i, r := range tagRows {
		tags[i] = r.str("tag")
	}
	task["last_activity"], task["edited_by"], task["requests"], task["tags"] = ts(active), t.strOrNil("edited_by"), linked, tags
	task["discarded"], task["report"] = nil, nil
	if state == "reported" {
		task["report"] = map[string]any{"by": t.str("owner"), "evidence": t.str("report"), "at": ts(t["reported_at"])}
	}
	if t.str("discarded_by") != "" {
		d := map[string]any{"by": t.str("discarded_by"), "reason": t.str("discard_reason"), "at": ts(t["discarded_at"]), "withdrew": []any{}, "batch": nil}
		ev, err := queryOne(e.DB, "SELECT payload FROM events WHERE kind = 'discard_task' AND "+eventTask+" = ? ORDER BY id DESC LIMIT 1", t.i("id"))
		if err != nil {
			return nil, err
		}
		p := decodeObject(ev.str("payload"))
		if w, ok := p["withdrew"].([]any); ok {
			d["withdrew"] = w
		}
		d["batch"] = p["batch"]
		task["discarded"] = d
	}
	task["mismatch"] = taskMismatch(t, state, reqs)
	if t.i("timeout_s") != 0 {
		task["time_box"] = duration(t.i("timeout_s"))
	}
	if len(blockedBy) > 0 {
		task["block_reason"] = "waiting on " + tids(blockedBy)
	}
	return task, nil
}

func tids(ids []int64) string {
	out := make([]string, len(ids))
	for i, id := range ids {
		out[i] = fmt.Sprintf("T%d", id)
	}
	return strings.Join(out, ", ")
}

// waitingOf is the spec's Task.waiting for an open task: behind its open blockers (a child blocks its parent), else on an open request to
// the founder, else the cause the supervisor's sweep stored with its reason. nil when nothing waits.
func (e *Env) waitingOf(t row, state string, waits []row, rec map[string]any) (any, error) {
	if state != "queued" && state != "doing" {
		return nil, nil
	}
	var on []int64
	var since int64
	for _, w := range waits {
		if w.i("open") == 1 {
			on = append(on, w.i("task"))
			if since == 0 || w.i("created_at") < since {
				since = w.i("created_at")
			}
		}
	}
	if len(on) > 0 {
		sort.Slice(on, func(i, j int) bool { return on[i] < on[j] })
		return map[string]any{"on": "task", "text": "blocked by " + tids(on), "tasks": on, "since": ts(since)}, nil
	}
	asked, err := queryOne(e.DB, "SELECT MIN(r.ts) AS since FROM requests r JOIN task_requests l ON l.request_id = r.id "+
		"WHERE l.task_id = ? AND r.target = ? AND "+founderOpen, t.i("id"), human)
	if err != nil {
		return nil, err
	}
	if asked.set("since") {
		return map[string]any{"on": "founder", "text": "waiting on you", "tasks": []int64{}, "since": ts(asked.i("since"))}, nil
	}
	if len(rec) == 0 {
		return nil, nil
	}
	cause, _ := rec["on"].(string)
	if cause == "" {
		cause = "owner" // kept before the sweep stored a cause
	}
	since, _ = recInt(rec["since"])
	return map[string]any{"on": cause, "text": pyStr(rec["why"]), "tasks": []int64{}, "since": ts(since)}, nil
}

// recInt is a whole number of a decoded record.
func recInt(v any) (int64, bool) {
	n, ok := v.(interface{ Int64() (int64, error) })
	if !ok {
		return 0, false
	}
	i, err := n.Int64()
	return i, err == nil
}

// taskMismatch is where a task's state and its delivery disagree, or nil, as actions.task_mismatch has it.
func taskMismatch(t row, state string, reqs []row) any {
	if state == "done" || state == "discarded" {
		return nil
	}
	sent := t.set("sent_at")
	if t.str("queued_by") == "" && sent {
		for _, r := range reqs {
			if r.str("state") == "queued" || (r.str("state") == "landed" && r.str("kind") == "task" && r.set("queue_order")) {
				return fmt.Sprintf("draft with work out (#%d) - its opener has not queued it", r.i("id"))
			}
		}
		return "draft with work out - its opener has not queued it"
	}
	if state == "queued" && !sent && t.str("owner") != human {
		return "approved, but no request is out"
	}
	return nil
}

// holdRec is the record the supervisor's sweep keeps of why a waiting task waits (supervisor/holds.go); empty when there is none.
func (e *Env) holdRec(tid int64) (map[string]any, error) {
	m, err := queryOne(e.DB, "SELECT value FROM meta WHERE name = ?", fmt.Sprintf("taskhold:%d", tid))
	if err != nil {
		return nil, err
	}
	return decodeObject(m.str("value")), nil
}

// waitLine is what shows beside a waiting task: the reason the sweep recorded for it and how long it has stood. A queued task nothing has
// recorded a reason for says it is not looked at yet.
func waitLine(rec map[string]any, state string) any {
	if state != "queued" && state != "doing" {
		return nil
	}
	if len(rec) == 0 {
		if state != "queued" {
			return nil
		}
		return "waiting: the daemon has not looked at it yet - it does on its next tick"
	}
	since, ok := recInt(rec["since"])
	if !ok {
		since = time.Now().Unix()
	}
	secs := max(time.Now().Unix()-since, 0)
	return fmt.Sprintf("waiting: %s (for %s)", pyStr(rec["why"]), ago(secs))
}

// ago is a duration in the largest unit that still says something, as infra/timefmt.ago has it.
func ago(secs int64) string {
	switch {
	case secs < 120:
		return fmt.Sprintf("%ds", secs)
	case secs < 5400:
		return fmt.Sprintf("%dm", secs/60)
	case secs < 86400:
		return fmt.Sprintf("%dh", secs/3600)
	}
	return span(secs)
}

// span is a long time as days and hours, as infra/timefmt.span has it from a day up.
func span(secs int64) string { return fmt.Sprintf("%dd%dh", secs/86400, secs%86400/3600) }

// Tasks is the spec's Task of each of the tasks by id, as the board shows them now, without their bodies (the shape of tasks.list and
// task.get); a task that is gone is left out. The board is read once for all of them, which is what a change event carries.
func (e *Env) Tasks(ids []int64) (map[int64]map[string]any, error) {
	out := map[int64]map[string]any{}
	if len(ids) == 0 {
		return out, nil
	}
	b, err := e.loadBoard()
	if err != nil {
		return nil, err
	}
	for _, id := range ids {
		t, err := queryOne(e.DB, "SELECT * FROM tasks WHERE id = ?", id)
		if err != nil {
			return nil, err
		}
		if t == nil {
			continue
		}
		if out[id], err = e.taskOf(t, b.state(t)); err != nil {
			return nil, err
		}
	}
	return out, nil
}

func (e *Env) taskGet(c *rpc.Conn, params map[string]any) (any, error) {
	tid, err := optInt(params, "task")
	if err != nil || tid == nil {
		return nil, badParam("task is a task number", "task")
	}
	t, err := queryOne(e.DB, "SELECT * FROM tasks WHERE id = ?", *tid)
	if err != nil {
		return nil, err
	}
	if t == nil {
		nf := rpc.NotFoundError(fmt.Sprintf("no task T%d", *tid))
		nf.Data = map[string]any{"what": "task", "id": *tid}
		return nil, nf
	}
	b, err := e.loadBoard()
	if err != nil {
		return nil, err
	}
	task, err := e.taskOf(t, b.state(t))
	if err != nil {
		return nil, err
	}
	first, err := e.firstRequest(*tid)
	if err != nil {
		return nil, err
	}
	task["body"] = first.str("body")
	images, agent := attachments.New(e.Home), c != nil && c.Caller.Role == "agent"
	parts := func(text string) []any { return images.Parts(text, agent) }
	if p := parts(first.str("body")); p != nil {
		task["body_parts"] = p
	}
	if err := e.taskResultOf(*tid, task); err != nil {
		return nil, err
	}
	reqs, err := query(e.DB, "SELECT r.* FROM requests r JOIN task_requests l ON l.request_id = r.id WHERE l.task_id = ? ORDER BY r.id", *tid)
	if err != nil {
		return nil, err
	}
	deliveries := []any{}
	for _, r := range reqs {
		at := r["delivered_at"]
		if r.i("delivered_at") == 0 {
			at = r["ts"]
		}
		how := r.str("kind")
		if how == "" {
			how = "request"
		}
		deliveries = append(deliveries, map[string]any{"at": ts(at), "how": how, "outcome": r.str("state")})
	}
	task["deliveries"] = deliveries
	events, err := query(e.DB, "SELECT id, ts, agent, kind, payload FROM events WHERE "+eventTask+" = ? ORDER BY id", *tid)
	if err != nil {
		return nil, err
	}
	evs := []any{}
	for _, ev := range events {
		kind := "agent"
		if ev.str("agent") == human {
			kind = "user"
		}
		evs = append(evs, map[string]any{
			"topic": "tasks", "type": ev.str("kind"), "at": ts(ev["ts"]),
			"actor": map[string]any{"kind": kind, "name": ev.str("agent")}, "agent": t.str("owner"), "title": nil,
			"target": map[string]any{"screen": "tasks", "id": fmt.Sprint(*tid)}, "data": decodeObject(ev.str("payload"))})
	}
	task["events"] = evs
	memos, err := query(e.DB, "SELECT * FROM task_memos WHERE task_id = ? ORDER BY id", *tid)
	if err != nil {
		return nil, err
	}
	ms := make([]any, len(memos))
	for i, m := range memos {
		if ms[i], err = memoJSON(e.DB, m, parts, images, agent); err != nil {
			return nil, err
		}
	}
	task["memos"] = ms
	if task["timeline"], err = e.taskTimeline(*tid, memos, reqs, parts); err != nil {
		return nil, err
	}
	blockers, err := e.blockers(*tid)
	if err != nil {
		return nil, err
	}
	bs := []any{}
	for _, bl := range blockers {
		bs = append(bs, map[string]any{"task": bl.i("id"), "owner": bl.str("owner"), "title": bl.str("title"),
			"reason": fmt.Sprintf("T%d (%s) %s", bl.i("id"), bl.str("owner"), bl.str("title"))})
	}
	task["blockers"] = bs
	kids, err := query(e.DB, "SELECT * FROM tasks WHERE parent_task = ? ORDER BY id", *tid)
	if err != nil {
		return nil, err
	}
	cs, subs := []any{}, []any{}
	for _, k := range kids {
		cs = append(cs, k.i("id"))
		subs = append(subs, map[string]any{"id": k.i("id"), "title": k.str("title"), "owner": k.str("owner"), "state": b.state(k)})
	}
	task["children"], task["subtasks"] = cs, subs
	task["merged_into"], task["parent_title"] = t.intOrNil("merged_into"), nil
	if t.set("parent_task") {
		if p, err := queryOne(e.DB, "SELECT title FROM tasks WHERE id = ?", t.i("parent_task")); err != nil {
			return nil, err
		} else if p != nil {
			task["parent_title"] = p.str("title")
		}
	}
	if task["people"], err = e.askable(t.str("owner")); err != nil {
		return nil, err
	}
	hands, err := query(e.DB, "SELECT ts, payload FROM events WHERE kind = 'task_handoff' AND "+eventTask+" = ? ORDER BY id", *tid)
	if err != nil {
		return nil, err
	}
	hs := []any{}
	for _, h := range hands {
		p := decodeObject(h.str("payload"))
		note := p["note"]
		if note == nil {
			note = ""
		}
		hs = append(hs, map[string]any{"at": ts(h["ts"]), "from": p["from"], "to": p["to"], "note": note})
	}
	task["handoffs"] = hs
	edits, err := query(e.DB, "SELECT ts, agent, payload FROM events WHERE kind = 'edit_task' AND "+eventTask+" = ? ORDER BY id", *tid)
	if err != nil {
		return nil, err
	}
	as := []any{} // every change of who works the task (the owner column) or who answers for it (the reporter), by an edit
	for _, ed := range edits {
		p := decodeObject(ed.str("payload"))
		for _, f := range [][2]string{{"owner", "assignee"}, {"reporter", "owner"}} {
			if c, ok := p[f[0]].(map[string]any); ok {
				as = append(as, map[string]any{"at": ts(ed["ts"]), "by": ed.str("agent"), "what": f[1], "from": c["old"], "to": c["new"]})
			}
		}
	}
	task["assignments"] = as
	return task, nil
}

// taskResultOf adds what the task's end kept (its commit and screenshots) and whether it was opened as a ui task.
func (e *Env) taskResultOf(tid int64, task map[string]any) error {
	res, err := taskresult.Get(e.DB, tid)
	if err != nil {
		return err
	}
	if !res.Empty() {
		out := map[string]any{}
		if res.Commit != "" {
			out["commit"], out["on_main"] = res.Commit, res.OnMain
		}
		shots := make([]any, len(res.Shots))
		for i, s := range res.Shots {
			shots[i] = map[string]any{"n": s.N, "bytes": s.Bytes}
		}
		out["shots"] = shots
		task["result"] = out
	}
	ui, err := taskresult.IsUI(e.DB, tid)
	if err != nil {
		return err
	}
	if ui {
		task["ui"] = true
	}
	return nil
}

func memoJSON(q db.Q, m row, parts func(string) []any, store *attachments.Store, withPath bool) (map[string]any, error) {
	var edited any
	if m.set("edited_at") {
		edited = ts(m["edited_at"])
	}
	memo := map[string]any{"id": m.i("id"), "task": m.i("task_id"), "author": m.str("author"),
		"at": ts(m["created_at"]), "text": m.str("body"), "edited_at": edited}
	if p := parts(m.str("body")); p != nil {
		memo["parts"] = p
	}
	infos, err := attachments.MemoFiles(q, m.i("id"))
	if err != nil {
		return nil, err
	}
	if len(infos) > 0 {
		files := make([]any, len(infos))
		for i, in := range infos {
			files[i] = store.FileMap(in, withPath)
		}
		memo["files"] = files
	}
	return memo, nil
}

// task.memo.list: a task's memos, oldest first. With limit, the newest limit memos, or the limit just older than before.
func (e *Env) taskMemoList(c *rpc.Conn, params map[string]any) (any, error) {
	tid, err := optInt(params, "task")
	if err != nil || tid == nil {
		return nil, badParam("task is a task number", "task")
	}
	before, err := rangedInt(params, "before", 1, 0)
	if err != nil {
		return nil, err
	}
	limit, err := rangedInt(params, "limit", 1, maxLimit)
	if err != nil {
		return nil, err
	}
	if before != nil && limit == nil {
		return nil, badParam("before needs limit", "limit")
	}
	t, err := queryOne(e.DB, "SELECT id FROM tasks WHERE id = ?", *tid)
	if err != nil {
		return nil, err
	}
	if t == nil {
		nf := rpc.NotFoundError(fmt.Sprintf("no task T%d", *tid))
		nf.Data = map[string]any{"what": "task", "id": *tid}
		return nil, nf
	}
	var b, n any
	if before != nil {
		b = *before
	}
	n = -1
	if limit != nil {
		n = *limit + 1
	}
	rows, err := query(e.DB, "SELECT * FROM (SELECT * FROM task_memos WHERE task_id = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?) ORDER BY id",
		*tid, b, b, n)
	if err != nil {
		return nil, err
	}
	more := limit != nil && int64(len(rows)) > *limit
	if more {
		rows = rows[1:]
	}
	images, agent := attachments.New(e.Home), c != nil && c.Caller.Role == "agent"
	parts := func(text string) []any { return images.Parts(text, agent) }
	memos := make([]any, len(rows))
	for i, m := range rows {
		if memos[i], err = memoJSON(e.DB, m, parts, images, agent); err != nil {
			return nil, err
		}
	}
	return map[string]any{"memos": memos, "more": more}, nil
}
