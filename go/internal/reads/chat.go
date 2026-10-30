package reads

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/handline/handline/go/internal/attachments"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/requestref"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/transcript"
)

// chat.messages, chat.requests, chat.transcript, chat.locate and chat.search follow handline/daemon/rpc_chat.py and
// handline/actions/chatreads.py: the requests between the founder and an agent (or all of an agent's), as pages.

const (
	chatPage      = 10
	searchDefault = 20
	searchMax     = 100
	perSource     = 500
	itemPrompt    = 1
	itemTask      = 2
)

// neverReached are the states of a request that no chat draws: withdrawn, replaced, denied.
const neverReached = "('denied', 'withdrawn', 'replaced')"

const requestSelect = "SELECT id, ref, ts, state_at, requester, target, state, body, " +
	"delivered_at, typed_at, landed_at, parent_id, quote, kind, " +
	"(SELECT group_concat(task_id) FROM task_requests WHERE request_id = requests.id) AS tasks, " +
	"(SELECT t.title FROM tasks t JOIN task_requests q ON q.task_id = t.id WHERE q.request_id = requests.id ORDER BY t.id LIMIT 1) AS task_title, " +
	"(SELECT MIN(task_id) FROM task_requests WHERE request_id = requests.id) AS task_id, " +
	"(SELECT json_extract(payload, '$.proposal') FROM events WHERE dedup_key = 'hr_outcome:' || requests.id) AS proposal FROM requests"

// chat is what one call reads with: the database and the task board, loaded when a task delivery needs it.
type chat struct {
	e     *Env
	b     *board
	loads int  // how many times b was loaded: once per call or pass
	agent bool // the caller is an agent: an image part carries its path
}

func isAgent(c *rpc.Conn) bool { return c != nil && c.Caller.Role == "agent" }

func (c *chat) taskState(taskID int64) (any, error) {
	t, err := queryOne(c.e.DB, "SELECT * FROM tasks WHERE id = ?", taskID)
	if err != nil || t == nil {
		return nil, err
	}
	if c.b == nil {
		c.loads++
		var err error
		if c.b, err = c.e.loadBoard(); err != nil {
			return nil, err
		}
	}
	return c.b.state(t), nil
}

// chatScope is the requests a chat draws. A notice the digest has listed is left out: the digest line carries it, and drawing both shows it twice.
func chatScope(agent string, everyone bool) (string, []any) {
	const undigested = " AND NOT (COALESCE(notice, 0) >= 1 AND delivered_at IS NOT NULL AND typed_at IS NULL)"
	if everyone {
		return "((requester = ? OR target = ?)" + undigested + ")", []any{agent, agent}
	}
	return "(((requester = ? AND target = ?) OR (requester = ? AND target = ?))" + undigested + ")", []any{human, agent, agent, human}
}

// rows adds the live state of the task a task delivery carries (the only row drawn as a card).
func (c *chat) withTaskState(rs []row) ([]row, error) {
	for _, r := range rs {
		r["task_state"] = nil
		if r["task_id"] != nil && r.str("kind") == "task" {
			st, err := c.taskState(r.i("task_id"))
			if err != nil {
				return nil, err
			}
			r["task_state"] = st
		}
	}
	return rs, nil
}

func (c *chat) requestRow(id int64) (row, error) {
	rs, err := query(c.e.DB, requestSelect+" WHERE id = ?", id)
	if err != nil || len(rs) == 0 {
		return nil, err
	}
	out, err := c.withTaskState(rs)
	return out[0], err
}

// page is a chat's requests, oldest first, and whether older ones remain; since reads from that id on.
func (c *chat) page(agent string, everyone bool, before, since *int64, limit int) ([]row, bool, error) {
	where, args := chatScope(agent, everyone)
	q := requestSelect + " WHERE state NOT IN " + neverReached + " AND " + where
	if since != nil {
		rs, err := query(c.e.DB, q+" AND id >= ? ORDER BY id", append(args, *since)...)
		if err != nil {
			return nil, false, err
		}
		rs, err = c.withTaskState(rs)
		return rs, false, err
	}
	if before != nil {
		q += " AND id < ?"
		args = append(args, *before)
	}
	rs, err := query(c.e.DB, q+" ORDER BY id DESC LIMIT ?", append(args, limit+1)...)
	if err != nil {
		return nil, false, err
	}
	more := len(rs) > limit
	if more {
		rs = rs[:limit]
	}
	for i, j := 0, len(rs)-1; i < j; i, j = i+1, j-1 {
		rs[i], rs[j] = rs[j], rs[i]
	}
	rs, err = c.withTaskState(rs)
	return rs, more, err
}

// after is the limit requests just newer than id, oldest first, and whether newer ones remain.
func (c *chat) after(agent string, everyone bool, id int64, limit int) ([]row, bool, error) {
	where, args := chatScope(agent, everyone)
	rs, err := query(c.e.DB, requestSelect+" WHERE state NOT IN "+neverReached+" AND "+where+
		" AND id > ? ORDER BY id LIMIT ?", append(args, id, limit+1)...)
	if err != nil {
		return nil, false, err
	}
	more := len(rs) > limit
	if more {
		rs = rs[:limit]
	}
	rs, err = c.withTaskState(rs)
	return rs, more, err
}

func landingOf(r row) string {
	switch r.str("state") {
	case "denied", "withdrawn", "replaced":
		return "withdrawn"
	}
	state := "queued"
	if r.i("landed_at") != 0 {
		state = "delivered"
	} else if r.i("typed_at") != 0 {
		state = "typed"
	}
	return state
}

func orEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}

// receiptOf is the receipt of a message to a structured agent: the state its session's ledger holds for the message, nil for a message
// the session never took. Landing follows it: queued is queued, sent and unknown are typed, confirmed is delivered.
func (c *chat) receiptOf(r row, landing string) (map[string]any, string, error) {
	switch landing {
	case "queued", "typed", "delivered":
	default:
		return nil, landing, nil
	}
	op, err := queryOne(c.e.DB, "SELECT state, result_json FROM operations WHERE kind = 'send' AND id = ?",
		"send:"+r.str("target")+":request:"+strconv.FormatInt(r.i("id"), 10))
	if err != nil {
		return nil, landing, err
	}
	state := op.str("state")
	if !op.set("state") || state == "withdrawn" {
		return nil, landing, nil
	}
	var kept struct {
		UUID string `json:"uuid"`
	}
	json.Unmarshal([]byte(op.str("result_json")), &kept)
	switch state {
	case "queued":
		landing = "queued"
	case "confirmed":
		if landing == "queued" || landing == "typed" {
			landing = "delivered"
		}
	default: // sent, unknown, and a word a later daemon adds
		landing = "typed"
	}
	return map[string]any{"state": state, "uuid": kept.UUID}, landing, nil
}

// decisionActions are the one-tap buttons a message to the founder carries for its task, from the task's state alone: an open task of the
// founder's is answered OK or No (No hands it back to whoever opened it).
func decisionActions(id int64, t row) []any {
	act := func(key, label, verb string, params map[string]any, needs string) any {
		params["task"] = id
		a := map[string]any{"key": key, "label": label, "verb": verb, "task": id, "params": params}
		if needs != "" {
			a["needs"] = needs
		}
		return a
	}
	if t.str("done_evidence") != "" || t.str("discarded_by") != "" {
		return nil
	}
	if t.str("owner") != human {
		return nil
	}
	out := []any{act("ok", "OK", "task.done", map[string]any{"evidence": "OK"}, "")}
	if opener := t.str("opened_by"); opener != human {
		out = append(out, act("no", "No", "task.handoff", map[string]any{"to": opener, "note": "No"}, ""))
	}
	return out
}

func (c *chat) messageOf(r row) (map[string]any, error) {
	state := landingOf(r)
	receipt, state, err := c.receiptOf(r, state)
	if err != nil {
		return nil, err
	}
	tasks := []any{}
	var ids []int64
	for _, s := range strings.Split(r.str("tasks"), ",") {
		if n, err := strconv.ParseInt(s, 10, 64); err == nil {
			ids = append(ids, n)
		}
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	var actions []any
	for _, id := range ids {
		t, err := queryOne(c.e.DB, "SELECT title, owner, opened_by, reporter, done_evidence, discarded_by FROM tasks WHERE id = ?", id)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, map[string]any{"id": id, "title": t.str("title")})
		if r.str("target") == human {
			actions = append(actions, decisionActions(id, t)...)
		}
	}
	var why any
	if (state == "queued" || state == "typed") && r.str("target") != human {
		var err error
		if why, err = c.e.QueueReason(r.str("target"), r.i("id")); err != nil {
			return nil, err
		}
		if receipt != nil && receipt["state"] == "unknown" {
			why = "unknown"
		}
	}
	stateAt := r["state_at"]
	if r.i("state_at") == 0 {
		stateAt = r["ts"]
	}
	m := map[string]any{"id": r.i("id"), "ref": r.str("ref"), "at": ts(r["ts"]), "state_at": ts(stateAt), "from": r.str("requester"), "to": r.str("target"),
		"state": r.str("state"), "landing": state, "sending_reason": why, "body": orEmpty(r["body"]),
		"delivered_at": ts(r["delivered_at"]), "typed_at": ts(r["typed_at"]), "landed_at": ts(r["landed_at"]),
		"parent": r.intOrNil("parent_id"), "kind": orEmpty(r["kind"]), "tasks": tasks}
	if r["quote"] != nil {
		m["quote"] = r.str("quote")
	}
	if len(actions) > 0 {
		m["actions"] = actions
	}
	if r.str("requester") == human && r.str("target") != human {
		x, err := queryOne(c.e.DB, "SELECT (SELECT id FROM tasks WHERE from_request = ? ORDER BY id LIMIT 1) AS task, no_work FROM requests WHERE id = ?", r.i("id"), r.i("id"))
		if err != nil {
			return nil, err
		}
		m["request_task"], m["no_work"] = x.intOrNil("task"), x.strOrNil("no_work")
	}
	if receipt != nil {
		m["receipt"] = receipt
	}
	if r["proposal"] != nil {
		m["proposal"] = r.i("proposal")
	}
	images := attachments.New(c.e.Home)
	if pictures := images.MessageMaps(c.e.DB, r.i("id"), r.str("body")); pictures != nil {
		m["attachments"] = pictures
	}
	if parts := images.MessageParts(c.e.DB, r.i("id"), r.str("body"), c.agent); parts != nil {
		m["parts"] = parts
	}
	return m, nil
}

func (c *chat) messagesOf(rs []row) ([]any, error) {
	out := make([]any, 0, len(rs))
	for _, r := range rs {
		m, err := c.messageOf(r)
		if err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, nil
}

// Param readers with the chat methods' messages.

func (e *Env) chatAgent(params map[string]any, required bool) (string, error) {
	v, has := params["agent"]
	if (!has || v == nil) && !required {
		return "", nil
	}
	name, ok := v.(string)
	if !ok || name == "" {
		return "", badParam("agent is an agent's name", "agent")
	}
	for _, n := range e.listAgents() {
		if n == name {
			return name, nil
		}
	}
	err := rpc.NotFoundError("no such agent: " + name)
	err.Data = map[string]any{"what": "agent", "name": name}
	return "", err
}

// chatScopeParam is whether the chat is the all-chat; present is false when scope was left out and it is not required.
func chatScopeParam(params map[string]any, required bool) (everyone, present bool, err error) {
	v, has := params["scope"]
	if (!has || v == nil) && !required {
		return false, false, nil
	}
	switch v {
	case "mine":
		return false, true, nil
	case "all":
		return true, true, nil
	}
	return false, false, badParam("scope is mine or all", "scope")
}

func rangedInt(params map[string]any, key string, lo, hi int64) (*int64, error) {
	v, has := params[key]
	if !has || v == nil {
		return nil, nil
	}
	n, ok := rpc.AsInt(v)
	if _, isBool := v.(bool); !ok || isBool || (lo != 0 && n < lo) || (hi != 0 && n > hi) {
		msg := key + " is an integer"
		if hi != 0 {
			msg += fmt.Sprintf(" from %d to %d", lo, hi)
		}
		return nil, badParam(msg, key)
	}
	return &n, nil
}

func limitOr(params map[string]any, hi int64, def int) (int, error) {
	n, err := rangedInt(params, "limit", 1, hi)
	if err != nil || n == nil {
		return def, err
	}
	return int(*n), nil
}

type chatQuery struct {
	everyone      bool
	before, since *int64
	limit         int
}

func readChatQuery(params map[string]any, def int) (chatQuery, error) {
	var q chatQuery
	var err error
	if q.everyone, _, err = chatScopeParam(params, true); err != nil {
		return q, err
	}
	if q.before, err = rangedInt(params, "before", 0, 0); err != nil {
		return q, err
	}
	if q.since, err = rangedInt(params, "since", 0, 0); err != nil {
		return q, err
	}
	if q.limit, err = limitOr(params, maxLimit, def); err != nil {
		return q, err
	}
	if q.before != nil && q.since != nil {
		return q, badParam("before and since are one or the other", "since")
	}
	return q, nil
}

func (e *Env) chatMessages(c *rpc.Conn, params map[string]any) (any, error) {
	q, err := readChatQuery(params, defaultLimit)
	if err != nil {
		return nil, err
	}
	agent, err := e.chatAgent(params, true)
	if err != nil {
		return nil, err
	}
	ch := &chat{e: e, agent: isAgent(c)}
	var rs []row
	var more bool
	if q.since != nil {
		rs, more, err = ch.after(agent, q.everyone, *q.since, q.limit)
	} else {
		rs, more, err = ch.page(agent, q.everyone, q.before, nil, q.limit)
	}
	if err != nil {
		return nil, err
	}
	for i, j := 0, len(rs)-1; i < j; i, j = i+1, j-1 {
		rs[i], rs[j] = rs[j], rs[i]
	}
	msgs, err := ch.messagesOf(rs)
	if err != nil {
		return nil, err
	}
	return map[string]any{"messages": msgs, "more": more}, nil
}

// What a task event moves a task to; doing is not stored, so the two events that put a task in its owner's hands stand for it. accept_task
// and reject_task are from when a done waited on an accept: they read as the done and the requeue they were.
var stateAfter = map[string]string{"draft_task": "draft", "queue_task": "queued", "task_begin": "doing", "task_start": "doing",
	"mark_task_done": "done", "close_task": "closed", "discard_task": "discarded", "reopen_task": "queued",
	"accept_task": "done", "reject_task": "queued"}

// stateAfterEvent is stateAfter for one event: a reopened discarded task is a draft until it is queued again; an accept that waited on a
// PR's merge ended nothing.
func stateAfterEvent(kind string, p map[string]any) (string, bool) {
	switch {
	case kind == "reopen_task" && p["was"] == "discarded":
		return "draft", true
	case kind == "accept_task" && p["pr"] != nil:
		return "", false
	}
	s, ok := stateAfter[kind]
	return s, ok
}

var chatEventKinds = []string{"draft_task", "queue_task", "task_begin", "task_start", "mark_task_done", "close_task", "discard_task",
	"reopen_task", "task_handoff", "edit_task"}

func placeholders(n int) string { return strings.TrimSuffix(strings.Repeat("?,", n), ",") }

func anySlice[T any](xs []T) []any {
	out := make([]any, len(xs))
	for i, x := range xs {
		out[i] = x
	}
	return out
}

// when is where a request sits in the chat: where its agent read it, else when it was made.
func when(r row) int64 {
	if n := r.i("landed_at"); n != 0 {
		return n
	}
	return r.i("ts")
}

func firstTask(r row) int64 {
	first := int64(-1)
	for _, s := range strings.Split(r.str("tasks"), ",") {
		if n, err := strconv.ParseInt(s, 10, 64); err == nil && (first < 0 || n < first) {
			first = n
		}
	}
	return first
}

func jsonStr(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return pyStr(v)
}

func editNotes(p map[string]any) []string {
	var notes []string
	if o, ok := p["owner"].(map[string]any); ok {
		notes = append(notes, "assigned to "+jsonStr(o["new"]))
	}
	if _, ok := p["title"]; ok {
		notes = append(notes, "title changed")
	}
	if o, ok := p["priority"].(map[string]any); ok {
		notes = append(notes, fmt.Sprintf("priority %s -> %s", jsonStr(o["old"]), jsonStr(o["new"])))
	}
	return notes
}

type step struct {
	at      int64
	task    int64
	who     string
	after   string
	change  string
	note    string
	request any
	ref     any
}

// taskEvents are the changes, since the first of rows, of the tasks delivered in them: a state it reached, a handoff, an edit.
func (c *chat) taskEvents(rs []row) ([]map[string]any, error) {
	cards := map[int64]string{}
	var ids []any
	for _, r := range rs {
		if r.str("kind") == "task" && r.str("tasks") != "" {
			t := firstTask(r)
			if _, seen := cards[t]; !seen {
				ids = append(ids, t)
			}
			cards[t] = r.str("task_title")
		}
	}
	if len(cards) == 0 {
		return nil, nil
	}
	since := int64(0)
	for i, r := range rs {
		if w := when(r); w != 0 && (i == 0 || since == 0 || w < since) {
			since = w
		}
	}
	evs, err := query(c.e.DB, "SELECT ts, agent, kind, payload, "+eventTask+" AS tid, "+
		"(SELECT ref FROM requests WHERE id = json_extract(payload, '$.request_id')) AS request_ref FROM events WHERE kind IN ("+placeholders(len(chatEventKinds))+
		") AND "+eventTask+" IN ("+placeholders(len(ids))+") ORDER BY id", append(anySlice(chatEventKinds), ids...)...)
	if err != nil {
		return nil, err
	}
	var steps []step
	for _, e := range evs {
		kind, p := e.str("kind"), decodeObject(e.str("payload"))
		if after, ok := stateAfterEvent(kind, p); ok {
			steps = append(steps, step{at: e.i("ts"), task: e.i("tid"), who: e.str("agent"), after: after})
			continue
		}
		if kind == "task_handoff" {
			steps = append(steps, step{at: e.i("ts"), task: e.i("tid"), who: e.str("agent"), change: "handed to " + jsonStr(p["to"]),
				note: jsonStr(orEmpty(p["note"])), request: p["request_id"], ref: e["request_ref"]})
			continue
		}
		for _, n := range editNotes(p) {
			steps = append(steps, step{at: e.i("ts"), task: e.i("tid"), who: e.str("agent"), change: n})
		}
	}
	for _, r := range rs {
		if r.str("kind") == "task" && r.str("tasks") != "" && landingState(r) == "delivered" && when(r) != 0 {
			steps = append(steps, step{at: when(r), task: firstTask(r), who: r.str("target"), after: "doing"})
		}
	}
	sort.SliceStable(steps, func(i, j int) bool { return steps[i].at < steps[j].at })
	state := map[int64]string{}
	var out []map[string]any
	for _, s := range steps {
		change := s.change
		if s.after != "" {
			before := state[s.task]
			if before == "" {
				before = "queued"
			}
			state[s.task] = s.after
			if before == s.after {
				continue
			}
			change = before + " -> " + s.after
		}
		if s.at >= since {
			out = append(out, map[string]any{"type": "task_event", "at": ts(s.at), "task": s.task, "title": cards[s.task], "who": s.who,
				"change": change, "note": s.note, "request": s.request, "request_ref": s.ref, "_ts": s.at})
		}
	}
	return out, nil
}

func landingState(r row) string {
	if r.i("landed_at") != 0 {
		return "delivered"
	}
	if r.i("typed_at") == 0 {
		return "queued"
	}
	return "typed"
}

func (c *chat) sessions(agent string, rs []row) ([]map[string]any, error) {
	since := int64(0)
	for i, r := range rs {
		if w := when(r); w != 0 && (i == 0 || since == 0 || w < since) {
			since = w
		}
	}
	starts, err := freshStarts(c.e.DB, "agent = ?", agent)
	if err != nil {
		return nil, err
	}
	var out []map[string]any
	for _, s := range starts {
		if s.i("ts") >= since {
			out = append(out, map[string]any{"type": "session", "at": ts(s["ts"]), "cause": s.str("cause"), "handoff": handoffOf(s), "_ts": s.i("ts")})
		}
	}
	return out, nil
}

// freshStarts are the session starts that began a conversation (not a resume) of the session_starts rows where says, oldest first.
func freshStarts(db *sql.DB, where string, args ...any) ([]row, error) {
	return query(db, "SELECT id, agent, ts, session_id, cause, text FROM session_starts WHERE resumed = 0 AND "+where+" ORDER BY id", args...)
}

// handoffOf is the text a session start was given, null for none.
func handoffOf(s row) any {
	if t := s.str("text"); t != "" {
		return t
	}
	return nil
}

// sessionItemOf is a session start as a transcript item; false until its session id is known.
func sessionItemOf(s row) (map[string]any, bool) {
	if !s.set("session_id") {
		return nil, false
	}
	return map[string]any{"id": itemID(s.i("ts"), transcript.ItemSession, s.i("id")), "at": ts(s["ts"]), "kind": "session",
		"session": map[string]any{"id": s.str("session_id"), "started": ts(s["ts"]), "handoff": handoffOf(s), "cause": s.str("cause")}}, true
}

// chatVersion is one string that changes whenever something in the chat does.
func (c *chat) version(agent string, everyone bool) (string, error) {
	where, args := chatScope(agent, everyone)
	r, err := queryOne(c.e.DB, "SELECT COUNT(*) AS a, COALESCE(MAX(id), 0) AS b, "+
		"COALESCE(MAX(state_at), 0) AS c, "+
		"COALESCE(MAX(delivered_at), 0) AS d, COUNT(delivered_at) AS e, COALESCE(MAX(landed_at), 0) AS f, COUNT(landed_at) AS g, "+
		"COALESCE(MAX(typed_at), 0) AS h, COALESCE(SUM(state = 'queued'), 0) AS i FROM requests WHERE state NOT IN "+neverReached+" AND "+where, args...)
	if err != nil {
		return "", err
	}
	ev, err := queryOne(c.e.DB, "SELECT COUNT(*) AS a, COALESCE(MAX(id), 0) AS b FROM events WHERE kind IN ("+placeholders(len(chatEventKinds))+")",
		anySlice(chatEventKinds)...)
	if err != nil {
		return "", err
	}
	ss, err := queryOne(c.e.DB, "SELECT COUNT(*) AS a, COALESCE(MAX(id), 0) AS b FROM session_starts WHERE agent = ?", agent)
	if err != nil {
		return "", err
	}
	h := sha256.Sum256([]byte(fmt.Sprint(r, ev, ss)))
	return hex.EncodeToString(h[:])[:24], nil
}

func (e *Env) chatRequests(c *rpc.Conn, params map[string]any) (any, error) {
	q, err := readChatQuery(params, chatPage)
	if err != nil {
		return nil, err
	}
	agent, err := e.chatAgent(params, true)
	if err != nil {
		return nil, err
	}
	cursor, err := queryOne(e.DB, "SELECT COALESCE(MAX(id), 0) AS n FROM stream_log")
	if err != nil {
		return nil, err
	}
	ch := &chat{e: e, agent: isAgent(c)}
	rs, more, err := ch.page(agent, q.everyone, q.before, q.since, q.limit)
	if err != nil {
		return nil, err
	}
	type item struct {
		m  map[string]any
		ts int64
		id int64
	}
	var items []item
	for _, r := range rs {
		msg, err := ch.messageOf(r)
		if err != nil {
			return nil, err
		}
		items = append(items, item{map[string]any{"type": "request", "at": ts(when(r)), "message": msg,
			"task_state": r["task_state"]}, when(r), r.i("id")})
	}
	evs, err := ch.taskEvents(rs)
	if err != nil {
		return nil, err
	}
	for _, m := range evs {
		items = append(items, item{m, m["_ts"].(int64), 0})
	}
	ses, err := ch.sessions(agent, rs)
	if err != nil {
		return nil, err
	}
	for _, m := range ses {
		items = append(items, item{m, m["_ts"].(int64), 0})
	}
	rank := func(it item) int {
		if it.m["type"] == "request" {
			return 0
		}
		return 1
	}
	sort.SliceStable(items, func(i, j int) bool {
		a, b := items[i], items[j]
		if a.ts != b.ts {
			return a.ts < b.ts
		}
		if rank(a) != rank(b) {
			return rank(a) < rank(b)
		}
		return a.id < b.id
	})
	out := make([]any, len(items))
	for i, it := range items {
		delete(it.m, "_ts")
		out[i] = it.m
	}
	ver, err := ch.version(agent, q.everyone)
	if err != nil {
		return nil, err
	}
	return map[string]any{"items": out, "more": more, "version": ver, "cursor": cursor.i("n")}, nil
}

// ownerOfChat is the agent whose chat a request is part of when none is named: its non-person end, the target first.
func ownerOfChat(r row) string {
	for _, k := range []string{"target", "requester"} {
		if n := r.str(k); n != human {
			return n
		}
	}
	return ""
}

type located struct {
	kind    string // here, chat or alone
	request int64
	agent   string
	tab     string
}

// locateMessage is the chat the request numbered mid is drawn in.
func (e *Env) locateMessage(mid int64, agent string, everyone bool) (located, error) {
	r, err := queryOne(e.DB, "SELECT id, requester, target, state FROM requests WHERE id = ?", mid)
	if err != nil || r == nil {
		return located{kind: "alone"}, err
	}
	req := r.i("id")
	switch r.str("state") {
	case "denied", "withdrawn", "replaced":
		return located{kind: "alone", request: req}, nil
	}
	ends := []string{r.str("requester"), r.str("target")}
	founders := ends[0] == human || ends[1] == human
	if ends[0] == agent || ends[1] == agent {
		tab := "mine"
		if everyone || !founders {
			tab = "all"
		}
		return located{"here", req, agent, tab}, nil
	}
	var elsewhere []string
	for _, n := range ends {
		if n != human {
			elsewhere = append(elsewhere, n)
		}
	}
	if len(elsewhere) == 0 {
		return located{kind: "alone", request: req}, nil
	}
	tab := "all"
	if founders {
		tab = "mine"
	}
	return located{"chat", req, elsewhere[0], tab}, nil
}

func (e *Env) chatLocate(c *rpc.Conn, params map[string]any) (any, error) {
	mid, err := requestref.Resolve(e.DB, e.Home, params["request"], requestref.Viewer(c), "request")
	if err != nil {
		return nil, err
	}
	limit, err := limitOr(params, maxLimit, defaultLimit)
	if err != nil {
		return nil, err
	}
	noMessage := func(msg string) error {
		err := rpc.NotFoundError(msg)
		err.Data = map[string]any{"what": "message", "name": strconv.FormatInt(mid, 10)}
		return err
	}
	r, err := queryOne(e.DB, "SELECT * FROM requests WHERE id = ?", mid)
	if err != nil {
		return nil, err
	}
	if r == nil {
		return nil, noMessage(fmt.Sprintf("no message %d", mid))
	}
	agent, err := e.chatAgent(params, false)
	if err != nil {
		return nil, err
	}
	if agent == "" {
		agent = ownerOfChat(r)
	}
	nothing := fmt.Sprintf("no chat shows message %d", mid)
	if agent == "" {
		return nil, noMessage(nothing)
	}
	found, err := e.locateMessage(mid, agent, false)
	if err != nil {
		return nil, err
	}
	if found.kind == "alone" {
		return nil, noMessage(nothing)
	}
	chatAgent, everyone := found.agent, found.tab == "all"
	ch := &chat{e: e, agent: isAgent(c)}
	back0 := found.request + 1
	back, older, err := ch.page(chatAgent, everyone, &back0, nil, limit/2+1)
	if err != nil {
		return nil, err
	}
	ahead, newer, err := ch.after(chatAgent, everyone, found.request, limit-len(back))
	if err != nil {
		return nil, err
	}
	all := append(append([]row{}, back...), ahead...)
	for i, j := 0, len(all)-1; i < j; i, j = i+1, j-1 {
		all[i], all[j] = all[j], all[i]
	}
	page, err := ch.messagesOf(all)
	if err != nil {
		return nil, err
	}
	var message any
	for _, m := range page {
		if m.(map[string]any)["id"] == found.request {
			message = m
			break
		}
	}
	scope := "mine"
	if everyone {
		scope = "all"
	}
	// The transcript of the same stretch of time, so the client draws the window with no second call: from the second of the oldest message
	// to the end of that of the newest, or on to now when nothing is newer.
	from, to := all[len(all)-1].i("ts")*1_000_000, int64(0)
	if newer {
		to = (all[0].i("ts") + 1) * 1_000_000
	}
	items, cutOlder, cutNewer, err := e.transcriptSpan(chatAgent, from, to, r.i("ts")*1_000_000)
	if err != nil {
		return nil, err
	}
	return map[string]any{"agent": chatAgent, "scope": scope, "message": message, "messages": page, "more_older": older, "more_newer": newer,
		"transcript": items, "transcript_more_older": cutOlder, "transcript_more_newer": cutNewer}, nil
}

// transcriptSpan are agent's transcript items with ids from from up to to (0: no end), oldest first: up to maxLimit, kept around at.
// cutOlder and cutNewer are whether the span has more at that end, for chat.transcript before and since to go on from.
func (e *Env) transcriptSpan(agent string, from, to, at int64) (items []any, cutOlder, cutNewer bool, err error) {
	// The tool calls are read from at both ways, a page each; where a page is full, what lies past its last call is not read, so the span
	// ends there and says it was cut.
	older, err := transcript.Tools(e.DB, agent, transcript.ToolWindow{Before: at, Limit: maxLimit + 1})
	if err != nil {
		return nil, false, false, err
	}
	newer, err := transcript.Tools(e.DB, agent, transcript.ToolWindow{Since: at, Limit: maxLimit + 1})
	if err != nil {
		return nil, false, false, err
	}
	if len(older) > maxLimit && older[0].ID > from {
		from, cutOlder = older[0].ID, true
	}
	if len(newer) > maxLimit && (to == 0 || newer[len(newer)-1].ID < to) {
		to, cutNewer = newer[len(newer)-1].ID+1, true
	}
	all, err := e.transcriptItems(agent, append(older, newer...))
	if err != nil {
		return nil, false, false, err
	}
	var span []map[string]any
	for _, it := range all {
		if id := it["id"].(int64); id >= from && (to == 0 || id < to) {
			span = append(span, it)
		}
	}
	if len(span) > maxLimit {
		mid := sort.Search(len(span), func(i int) bool { return span[i]["id"].(int64) >= at })
		lo := max(0, min(mid-maxLimit/2, len(span)-maxLimit))
		cutOlder, cutNewer = cutOlder || lo > 0, cutNewer || lo+maxLimit < len(span)
		span = span[lo : lo+maxLimit]
	}
	fitToolOutput(span)
	items = make([]any, len(span))
	for i, it := range span {
		items[i] = it
	}
	return items, cutOlder, cutNewer, nil
}

func (e *Env) chatSearch(c *rpc.Conn, params map[string]any) (any, error) {
	query_, ok := params["query"].(string)
	if !ok || strings.TrimSpace(query_) == "" {
		return nil, badParam("query is text and not empty", "query")
	}
	limit, err := limitOr(params, searchMax, searchDefault)
	if err != nil {
		return nil, err
	}
	everyone, scoped, err := chatScopeParam(params, false)
	if err != nil {
		return nil, err
	}
	agent, err := e.chatAgent(params, false)
	if err != nil {
		return nil, err
	}
	if isAgent(c) {
		// An agent searches its own messages only, every one it sent or received unless it asks for mine.
		me, _ := c.Caller.Info["name"].(string)
		if agent != "" && agent != me {
			return nil, rpc.Refused("an agent searches only its own messages", "role", "leave out agent")
		}
		agent, everyone = me, everyone || !scoped
	}
	with, err := optStr(params, "with")
	if err != nil {
		return nil, err
	}
	since, err := optTime(params, "since")
	if err != nil {
		return nil, err
	}
	like := "%" + strings.NewReplacer(`\`, `\\`, "%", `\%`, "_", `\_`).Replace(strings.TrimSpace(query_)) + "%"
	where, scopeArgs := "1 = 1", []any{}
	if agent != "" {
		where, scopeArgs = chatScope(agent, everyone)
	}
	switch {
	case with != nil && agent != "":
		where += " AND ((requester = ? AND target = ?) OR (requester = ? AND target = ?))"
		scopeArgs = append(scopeArgs, agent, *with, *with, agent)
	case with != nil:
		where += " AND (requester = ? OR target = ?)"
		scopeArgs = append(scopeArgs, *with, *with)
	}
	if since != nil {
		where += " AND ts >= ?"
		scopeArgs = append(scopeArgs, *since)
	}
	hits, err := query(e.DB, "SELECT id FROM requests WHERE state NOT IN "+neverReached+" AND "+where+
		` AND body LIKE ? ESCAPE '\' `+
		"ORDER BY id DESC LIMIT ?", append(scopeArgs, like, limit)...)
	if err != nil {
		return nil, err
	}
	ch := &chat{e: e, agent: isAgent(c)}
	out := []any{}
	for _, h := range hits {
		r, err := queryOne(e.DB, "SELECT requester, target FROM requests WHERE id = ?", h.i("id"))
		if err != nil {
			return nil, err
		}
		chatAgent := agent
		if chatAgent == "" {
			chatAgent = ownerOfChat(r)
		}
		if chatAgent == "" {
			continue
		}
		found, err := e.locateMessage(h.i("id"), chatAgent, everyone)
		if err != nil {
			return nil, err
		}
		if found.kind == "alone" {
			continue
		}
		shown, tab := found.agent, found.tab
		full, err := ch.requestRow(h.i("id"))
		if err != nil {
			return nil, err
		}
		m, err := ch.messageOf(full)
		if err != nil {
			return nil, err
		}
		m["agent"], m["scope"] = shown, tab
		out = append(out, m)
	}
	return map[string]any{"messages": out}, nil
}

// ---------------------------------------------------------------- transcript

func itemID(at int64, source int64, n int64) int64 { return transcript.ItemID(at, source, n) }

var transcriptEvents = map[string]string{"task_new": "opened", "task_begin": "changed", "task_start": "changed", "edit_task": "changed",
	"task_handoff": "changed", "queue_task": "changed", "draft_task": "changed", "reopen_task": "changed", "release_task": "changed",
	"merge_task": "changed", "mark_task_done": "done", "discard_task": "withdrawn", "close_task": "withdrawn"}

// batchKind is a batch discard's one event: the task_batch card, apart from transcriptEvents since it names no one task.
const batchKind = "discard_batch"

// promptItemOf is a prompt block as a transcript item. A closed one carries its answer: what prompt.answer kept on the block (answer_label,
// at answered_at), else {outside: true} from when the daemon saw it close, since it was answered in the terminal.
func promptItemOf(agent string, b row) map[string]any {
	item := map[string]any{"id": itemID(b.i("first_seen"), itemPrompt, b.i("id")), "at": ts(b["first_seen"]), "kind": "prompt",
		"prompt": promptOf(agent, b), "open": !b.set("gone_at")}
	switch {
	case b.str("answer_label") != "":
		answer := map[string]any{"label": b.str("answer_label"), "by": b.str("answer_by"), "at": ts(b["answered_at"])}
		if b.str("answer_note") != "" {
			answer["note"] = b.str("answer_note")
		}
		item["answer"] = answer
	case b.set("gone_at"):
		item["answer"] = map[string]any{"outside": true, "at": ts(b["gone_at"])}
	}
	return item
}

// taskItemOf is one task event of the events table as a transcript item and the task it names; false when the chat does not show it. A
// batch discard is one task_batch item naming no one task, shown in its author's chat.
func taskItemOf(ev row) (item map[string]any, task int64, ok bool) {
	event, known := transcriptEvents[ev.str("kind")]
	p := decodeObject(ev.str("payload"))
	id := itemID(ev.i("ts"), itemTask, ev.i("id"))
	if ev.str("kind") == batchKind {
		var tasks []any
		list, _ := p["tasks"].([]any)
		for _, x := range list {
			t, _ := x.(map[string]any)
			tid, _ := rpc.AsInt(t["task_id"])
			tasks = append(tasks, map[string]any{"task": tid, "title": text(t["title"]), "reason": text(t["reason"])})
		}
		return map[string]any{"id": id, "at": ts(ev["ts"]), "kind": "task_batch",
			"task_batch": map[string]any{"batch": text(p["batch"]), "who": ev.str("agent"), "tasks": tasks}}, 0, true
	}
	tid, isInt := rpc.AsInt(p["task_id"])
	if !known || !isInt {
		return nil, 0, false
	}
	var evidence any
	if event == "done" {
		evidence = p["done"]
	} else if event == "withdrawn" {
		evidence = p["reason"]
	}
	body := map[string]any{"event": event, "task": tid, "request": p["request_id"], "evidence": evidence, "who": ev.str("agent")}
	if change := changeOf(ev.str("kind"), p); change != "" {
		body["change"] = change
	}
	return map[string]any{"id": id, "at": ts(ev["ts"]), "kind": "task_event", "task_event": body}, tid, true
}

// inBatchCard is whether a task event is already on the batch card agent's chat shows: a discard of a batch agent made.
func inBatchCard(ev row, agent string) bool {
	return ev.str("kind") == "discard_task" && ev.str("agent") == agent && text(decodeObject(ev.str("payload"))["batch"]) != ""
}

// changeOf is what a "changed" event changed, in the words chat.requests gives it: an edit's fields, "handed to X". "" for an event that says
// no more than its event word does.
func changeOf(kind string, p map[string]any) string {
	switch kind {
	case "edit_task":
		return strings.Join(editNotes(p), ", ")
	case "task_handoff":
		return "handed to " + jsonStr(p["to"])
	}
	return ""
}

// transcriptItems are agent's transcript items, oldest first: its replies, the tool calls given, its prompt cards, its session starts
// and its tasks' events, each source up to perSource of its newest.
func (e *Env) transcriptItems(agent string, tools []transcript.Tool) ([]map[string]any, error) {
	var items []map[string]any
	// A structured session's text blocks are its replies: each is a transcript event, and the event holds this item's id.
	texts, err := transcript.Replies(e.DB, agent, perSource)
	if err != nil {
		return nil, err
	}
	for _, t := range texts {
		items = append(items, map[string]any{"id": t.ID, "at": ts(t.At), "kind": "reply", "text": t.Text})
	}
	for _, t := range tools {
		item := map[string]any{"id": t.ID, "at": ts(t.At), "kind": "tool", "tool": t.Name, "text": t.Text}
		if t.Output != nil {
			item["output"] = *t.Output
			if t.Dropped > 0 {
				item["dropped_lines"] = t.Dropped
			}
		}
		items = append(items, item)
	}
	blocks, err := query(e.DB, "SELECT * FROM prompt_blocks WHERE agent = ? ORDER BY id DESC LIMIT ?", agent, perSource)
	if err != nil {
		return nil, err
	}
	for _, b := range blocks {
		items = append(items, promptItemOf(agent, b))
	}
	starts, err := freshStarts(e.DB, "agent = ?", agent)
	if err != nil {
		return nil, err
	}
	for _, s := range starts {
		if item, ok := sessionItemOf(s); ok {
			items = append(items, item)
		}
	}
	kinds := ChatTaskKinds()
	evs, err := query(e.DB, "SELECT id, ts, agent, kind, payload FROM events WHERE kind IN ("+placeholders(len(kinds))+") AND ("+eventTask+
		" IN (SELECT id FROM tasks WHERE owner = ?) OR (kind = '"+batchKind+"' AND agent = ?)) ORDER BY id DESC LIMIT ?", append(anySlice(kinds), agent, agent, perSource)...)
	if err != nil {
		return nil, err
	}
	for _, ev := range evs {
		if inBatchCard(ev, agent) {
			continue
		}
		if item, _, ok := taskItemOf(ev); ok {
			items = append(items, item)
		}
	}
	sort.SliceStable(items, func(i, j int) bool { return items[i]["id"].(int64) < items[j]["id"].(int64) })
	return items, nil
}

func (e *Env) chatTranscript(c *rpc.Conn, params map[string]any) (any, error) {
	before, err := rangedInt(params, "before", 0, 0)
	if err != nil {
		return nil, err
	}
	since, err := rangedInt(params, "since", 0, 0)
	if err != nil {
		return nil, err
	}
	limit, err := limitOr(params, maxLimit, defaultLimit)
	if err != nil {
		return nil, err
	}
	if before != nil && since != nil {
		return nil, badParam("before and since are one or the other", "since")
	}
	agent, err := e.chatAgent(params, true)
	if err != nil {
		return nil, err
	}
	window := transcript.ToolWindow{Limit: limit + 1} // what a before page keeps, and one more to say there is more
	if since != nil {
		window.Since, window.Limit = *since, maxLimit+1 // a since page keeps up to maxLimit of every source, whatever limit says
	} else if before != nil {
		window.Before = *before
	}
	tools, err := transcript.Tools(e.DB, agent, window)
	if err != nil {
		return nil, err
	}
	items, err := e.transcriptItems(agent, tools)
	if err != nil {
		return nil, err
	}
	more := false
	if since != nil { // from the second since is in: a source that wrote later in it has an id below it
		floor := *since / 1_000_000 * 1_000_000
		var kept []map[string]any
		for _, it := range items {
			if it["id"].(int64) >= floor {
				kept = append(kept, it)
			}
		}
		if len(kept) > maxLimit {
			kept = kept[:maxLimit]
		}
		items = kept
	} else {
		var kept []map[string]any
		for _, it := range items {
			if before == nil || it["id"].(int64) < *before {
				kept = append(kept, it)
			}
		}
		if more = len(kept) > limit; more {
			kept = kept[len(kept)-limit:]
		}
		items = kept
	}
	fitToolOutput(items)
	working, err := e.working(agent)
	if err != nil {
		return nil, err
	}
	out := make([]any, len(items))
	for i, it := range items {
		out[i] = it
	}
	return map[string]any{"items": out, "more": more, "working": working}, nil
}

// toolOutputBudget is the tool output, in bytes, one page carries between its items.
const toolOutputBudget = 2 << 20

// fitToolOutput holds a page's tool output to toolOutputBudget, newest item first: the one that crosses it is cut to what is left and
// the ones older than that carry none, each with the lines left out added to dropped_lines, so a cut is always said.
func fitToolOutput(items []map[string]any) {
	left := toolOutputBudget
	for i := len(items) - 1; i >= 0; i-- {
		out, ok := items[i]["output"].(string)
		if !ok {
			continue
		}
		kept, dropped := transcript.FitOutput(out, left)
		left -= len(kept)
		if dropped == 0 {
			continue
		}
		items[i]["output"] = kept
		was, _ := items[i]["dropped_lines"].(int64)
		items[i]["dropped_lines"] = was + dropped
	}
}

// working is the spec's Working: whether the agent's turn is running, and the line that says why it is held up.
func (e *Env) working(agent string) (map[string]any, error) {
	stored, err := e.storedViews()
	if err != nil {
		return nil, err
	}
	raw, err := e.viewOf(agent, stored, nil)
	if err != nil {
		return nil, err
	}
	return workingFrom(decodeObject(string(raw))), nil
}

// workingFrom is an agent's view as the spec's Working.
func workingFrom(view map[string]any) map[string]any {
	var line any
	for _, k := range []string{"blocker", "state_detail"} {
		if truthy(view[k]) {
			line = view[k]
			break
		}
	}
	return map[string]any{"turn_running": strings.EqualFold(pyStr(view["state"]), "running"), "line": line, "since": view["state_since"]}
}
