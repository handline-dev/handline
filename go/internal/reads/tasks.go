package reads

import (
	"fmt"
	"sort"
	"strings"
	"time"

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

func (e *Env) loadBoard(open []row) (*board, error) {
	b := &board{begun: map[int64]bool{}, sent: map[int64]bool{}, children: map[int64][]int64{},
		current: map[string]int64{}, held: map[int64]row{}}
	rows, err := query(e.DB, "SELECT DISTINCT "+eventTask+" AS t FROM events WHERE kind = 'task_begin'")
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
	for _, t := range open {
		if t.set("parent_task") {
			b.children[t.i("parent_task")] = append(b.children[t.i("parent_task")], t.i("id"))
		}
	}
	if rows, err = query(e.DB, "SELECT agent, current_task FROM agent_state WHERE current_task IS NOT NULL"); err != nil {
		return nil, err
	}
	for _, r := range rows {
		b.current[r.str("agent")] = r.i("current_task")
	}
	if rows, err = query(e.DB, "SELECT id, discarded_by, done_evidence FROM tasks WHERE id IN "+
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
	return !ok || ended(cur)
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

// state is one of draft, queued, doing, done, discarded.
func (b *board) state(t row) string {
	if t.str("discarded_by") != "" {
		return "discarded"
	}
	if t.str("done_evidence") != "" {
		return "done"
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

// boardRows are the tasks in priority then number order: every one, or the open ones.
func (e *Env) boardRows(withEnded bool) (shown []row, open []row, err error) {
	all, err := query(e.DB, "SELECT * FROM tasks ORDER BY priority, id")
	if err != nil {
		return nil, nil, err
	}
	for _, t := range all {
		if !ended(t) {
			open = append(open, t)
		}
	}
	if withEnded {
		return all, open, nil
	}
	return open, open, nil
}

// reach is the agents whose tasks agent sees: itself and everyone under it, walked down the supervisor edges the
// stored views record (the Python daemon reads the same from team.yaml and writes them into agent_view each tick).
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
	var owners map[string]bool
	if !everyone && c.Caller.Role == "agent" {
		if owners, err = e.reach(c.Caller.Info["name"].(string)); err != nil {
			return nil, err
		}
	}
	if owner != nil && owners != nil && !owners[*owner] {
		return nil, rpc.Refused(fmt.Sprintf("%s is not you or one of your reports: everyone shows every team's tasks", *owner), "", "")
	}
	showEnded := withEnded || (state != nil && (*state == "done" || *state == "discarded"))
	shown, open, err := e.boardRows(showEnded)
	if err != nil {
		return nil, err
	}
	b, err := e.loadBoard(open)
	if err != nil {
		return nil, err
	}
	type item struct {
		t     row
		state string
	}
	var rows []item
	for _, t := range shown {
		st := b.state(t)
		if (owner != nil && t.str("owner") != *owner) || (owners != nil && !owners[t.str("owner")]) ||
			(state != nil && st != *state) || (opener != nil && t.str("opened_by") != *opener) ||
			(parent != nil && (!t.set("parent_task") || t.i("parent_task") != *parent)) ||
			(before != nil && t.i("id") >= *before) {
			continue
		}
		rows = append(rows, item{t, st})
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].t.i("id") > rows[j].t.i("id") })
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
	blockers, err := e.blockers(t.i("id"))
	if err != nil {
		return nil, err
	}
	hold, err := e.waitLine(t, state)
	if err != nil {
		return nil, err
	}
	task := map[string]any{
		"id": t.i("id"), "from": t.str("opened_by"), "to": t.str("owner"), "title": t.str("title"), "state": state,
		"relation": first.str("relation"), "response": first.str("response"), "time_box": nil,
		"parent": t.intOrNil("parent_task"), "created": ts(t["created_at"]), "delivered": ts(t["delivered_at"]),
		"done": ts(t["done_at"]), "priority": t.i("priority"), "hold_reason": hold, "block_reason": nil, "evidence": t.strOrNil("done_evidence"),
	}
	if t.i("timeout_s") != 0 {
		task["time_box"] = duration(t.i("timeout_s"))
	}
	if len(blockers) > 0 {
		ids := make([]string, len(blockers))
		for i, b := range blockers {
			ids[i] = fmt.Sprintf("T%d", b.i("id"))
		}
		task["block_reason"] = "waiting on " + strings.Join(ids, ", ")
	}
	return task, nil
}

// waitLine is what shows beside a waiting task: the reason the Python daemon's tick recorded for it and how long it
// has stood. This daemon is the one running, so a task nothing has recorded a reason for says it is not looked at yet.
func (e *Env) waitLine(t row, state string) (any, error) {
	if state != "queued" && state != "doing" {
		return nil, nil
	}
	m, err := queryOne(e.DB, "SELECT value FROM meta WHERE name = ?", fmt.Sprintf("taskhold:%d", t.i("id")))
	if err != nil {
		return nil, err
	}
	rec := decodeObject(m.str("value"))
	if len(rec) == 0 {
		if state != "queued" {
			return nil, nil
		}
		return "waiting: the daemon has not looked at it yet - it does on its next tick", nil
	}
	since := time.Now().Unix()
	if n, ok := rec["since"].(interface{ Int64() (int64, error) }); ok {
		since, _ = n.Int64()
	}
	secs := max(time.Now().Unix()-since, 0)
	return fmt.Sprintf("waiting: %s (for %s)", pyStr(rec["why"]), ago(secs)), nil
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
	_, open, err := e.boardRows(false)
	if err != nil {
		return nil, err
	}
	b, err := e.loadBoard(open)
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
	ms := []any{}
	for _, m := range memos {
		ms = append(ms, map[string]any{"id": m.i("id"), "task": m.i("task_id"), "author": m.str("author"),
			"at": ts(m["created_at"]), "text": m.str("body")})
	}
	task["memos"] = ms
	blockers, err := e.blockers(*tid)
	if err != nil {
		return nil, err
	}
	bs := []any{}
	for _, bl := range blockers {
		bs = append(bs, map[string]any{"task": bl.i("id"),
			"reason": fmt.Sprintf("T%d (%s) %s", bl.i("id"), bl.str("owner"), bl.str("title"))})
	}
	task["blockers"] = bs
	kids, err := query(e.DB, "SELECT id FROM tasks WHERE parent_task = ? ORDER BY id", *tid)
	if err != nil {
		return nil, err
	}
	cs := []any{}
	for _, k := range kids {
		cs = append(cs, k.i("id"))
	}
	task["children"] = cs
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
	return task, nil
}
