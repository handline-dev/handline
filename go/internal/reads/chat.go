package reads

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"

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
	itemReply     = 0
	itemPrompt    = 1
	itemTask      = 2
)

// neverReached are the states of a request that no chat draws: withdrawn, replaced, denied.
const neverReached = "('denied', 'withdrawn', 'replaced')"

const requestSelect = "SELECT id, ts, state_at, requester, target, state, body, " + answerSQL + ", " + dismissedSQL + ", " +
	"delivered_at, typed_at, landed_at, parent_id, kind, settles, " +
	"(SELECT group_concat(task_id) FROM task_requests WHERE request_id = requests.id) AS tasks, " +
	"(SELECT t.title FROM tasks t JOIN task_requests q ON q.task_id = t.id WHERE q.request_id = requests.id ORDER BY t.id LIMIT 1) AS task_title, " +
	"(SELECT MIN(task_id) FROM task_requests WHERE request_id = requests.id) AS task_id, " +
	"(SELECT a.id FROM requests a WHERE a.settles = requests.id ORDER BY a.id LIMIT 1) AS answer_id, " +
	"EXISTS (SELECT 1 FROM events WHERE dedup_key = 'task_question:' || requests.id) AS task_question FROM requests"

// chat is what one call reads with: the database and the task board, loaded when a task delivery needs it.
type chat struct {
	e *Env
	b *board
}

func (c *chat) taskState(taskID int64) (any, error) {
	t, err := queryOne(c.e.DB, "SELECT * FROM tasks WHERE id = ?", taskID)
	if err != nil || t == nil {
		return nil, err
	}
	if c.b == nil {
		_, open, err := c.e.boardRows(false)
		if err != nil {
			return nil, err
		}
		if c.b, err = c.e.loadBoard(open); err != nil {
			return nil, err
		}
	}
	return c.b.state(t), nil
}

func chatScope(agent string, everyone bool) (string, []any) {
	if everyone {
		return "(requester = ? OR target = ?)", []any{agent, agent}
	}
	return "((requester = ? AND target = ?) OR (requester = ? AND target = ?))", []any{human, agent, agent, human}
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
	q := requestSelect + " WHERE state NOT IN " + neverReached + " AND settles IS NULL AND " + where
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
	rs, err := query(c.e.DB, requestSelect+" WHERE state NOT IN "+neverReached+" AND settles IS NULL AND "+where+
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

func dismissal(r row) (bool, any, any) {
	if by := r.str("dismisser"); by != "" {
		return true, by, r.strOrNil("dismiss_reason")
	}
	text := r.str("answer")
	if text == legacyDismiss {
		return true, nil, nil
	}
	if strings.HasPrefix(text, legacyDismiss+": ") {
		return true, nil, text[len(legacyDismiss)+2:]
	}
	return false, nil, nil
}

func answeredAt(r row) any {
	for _, k := range []string{"answered_at", "state_at", "ts"} {
		if n := r.i(k); n != 0 {
			return r[k]
		}
	}
	return r["ts"]
}

func landingOf(r row) string {
	switch r.str("state") {
	case "denied", "withdrawn", "replaced":
		return "withdrawn"
	}
	if d, _, _ := dismissal(r); d {
		return "dismissed"
	}
	if r["answer"] != nil {
		return "answered"
	}
	state := "queued"
	if r.i("landed_at") != 0 {
		state = "delivered"
	} else if r.i("typed_at") != 0 {
		state = "typed"
	}
	if kind := r.str("kind"); state == "delivered" && kind != "" && kind != "say" {
		return "delivered_unanswered"
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
	case "queued", "typed", "delivered", "delivered_unanswered":
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

func (c *chat) messageOf(r row) (map[string]any, error) {
	state := landingOf(r)
	receipt, state, err := c.receiptOf(r, state)
	if err != nil {
		return nil, err
	}
	verdict, hasVerdict := shownVerdict(r)
	tasks := []any{}
	var ids []int64
	for _, s := range strings.Split(r.str("tasks"), ",") {
		if n, err := strconv.ParseInt(s, 10, 64); err == nil {
			ids = append(ids, n)
		}
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	for _, id := range ids {
		t, err := queryOne(c.e.DB, "SELECT title FROM tasks WHERE id = ?", id)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, map[string]any{"id": id, "title": t.str("title")})
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
	var answer any
	if r["answer_id"] != nil && hasVerdict && verdict != "" {
		answer = map[string]any{"id": r.i("answer_id"), "text": verdict, "at": ts(answeredAt(r))}
	}
	dismissed, _, _ := dismissal(r)
	m := map[string]any{"id": r.i("id"), "at": ts(r["ts"]), "state_at": ts(stateAt), "from": r.str("requester"), "to": r.str("target"),
		"state": r.str("state"), "landing": state, "sending_reason": why, "body": orEmpty(r["body"]), "answer": answer,
		"dismissed": dismissed, "delivered_at": ts(r["delivered_at"]), "typed_at": ts(r["typed_at"]), "landed_at": ts(r["landed_at"]),
		"parent": r.intOrNil("parent_id"), "kind": orEmpty(r["kind"]), "tasks": tasks, "is_task_question": r.i("task_question") != 0}
	if receipt != nil {
		m["receipt"] = receipt
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
	ch := &chat{e: e}
	var rs []row
	var more bool
	if q.since != nil {
		next := *q.since + 1
		rs, more, err = ch.page(agent, q.everyone, nil, &next, 0)
		if len(rs) > q.limit {
			rs = rs[len(rs)-q.limit:]
		}
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

// What a task event moves a task to; doing is not stored, so the two events that put a task in its owner's hands stand for it.
var stateAfter = map[string]string{"draft_task": "draft", "queue_task": "queued", "task_begin": "doing", "task_start": "doing",
	"mark_task_done": "done", "close_task": "closed", "discard_task": "discarded", "reopen_task": "queued"}

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
	evs, err := query(c.e.DB, "SELECT ts, agent, kind, payload, "+eventTask+" AS tid FROM events WHERE kind IN ("+placeholders(len(chatEventKinds))+
		") AND "+eventTask+" IN ("+placeholders(len(ids))+") ORDER BY id", append(anySlice(chatEventKinds), ids...)...)
	if err != nil {
		return nil, err
	}
	var steps []step
	for _, e := range evs {
		kind := e.str("kind")
		if after, ok := stateAfter[kind]; ok {
			steps = append(steps, step{at: e.i("ts"), task: e.i("tid"), who: e.str("agent"), after: after})
			continue
		}
		p := decodeObject(e.str("payload"))
		if kind == "task_handoff" {
			steps = append(steps, step{at: e.i("ts"), task: e.i("tid"), who: e.str("agent"), change: "handed to " + jsonStr(p["to"]),
				note: jsonStr(orEmpty(p["note"])), request: p["request_id"]})
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
				"change": change, "note": s.note, "request": s.request, "_ts": s.at})
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
	starts, err := query(c.e.DB, "SELECT ts, cause, resumed, text FROM session_starts WHERE agent = ? ORDER BY id", agent)
	if err != nil {
		return nil, err
	}
	var out []map[string]any
	for _, s := range starts {
		if s.i("ts") >= since && s.i("resumed") == 0 {
			var handoff any
			if t := s.str("text"); t != "" {
				handoff = t
			}
			out = append(out, map[string]any{"type": "session", "at": ts(s["ts"]), "cause": s.str("cause"), "handoff": handoff, "_ts": s.i("ts")})
		}
	}
	return out, nil
}

// chatVersion is one string that changes whenever something in the chat does.
func (c *chat) version(agent string, everyone bool) (string, error) {
	where, args := chatScope(agent, everyone)
	r, err := queryOne(c.e.DB, "SELECT COUNT(*) AS a, COALESCE(MAX(id), 0) AS b, "+
		"COALESCE(MAX(MAX(COALESCE(requests.state_at, 0), COALESCE((SELECT MAX(a.ts) FROM requests a WHERE a.settles = requests.id), 0))), 0) AS c, "+
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
	ch := &chat{e: e}
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
		dismissed, by, reason := dismissal(r)
		var dis any
		if dismissed {
			dis = map[string]any{"by": by, "reason": reason}
		}
		items = append(items, item{map[string]any{"type": "request", "at": ts(when(r)), "message": msg, "dismissal": dis,
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

// locateMessage is the chat mid (a request's number, or the number of the answer that settles one) is drawn in.
func (e *Env) locateMessage(mid int64, agent string, everyone bool) (located, error) {
	r, err := queryOne(e.DB, "SELECT id, requester, target, state, settles FROM requests WHERE id = ?", mid)
	if err != nil || r == nil {
		return located{kind: "alone"}, err
	}
	req := r.i("id")
	if r.set("settles") {
		req = r.i("settles")
		if r, err = queryOne(e.DB, "SELECT id, requester, target, state FROM requests WHERE id = ?", req); err != nil || r == nil {
			return located{kind: "alone"}, err
		}
	}
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
	mid, ok := rpc.AsInt(params["request"])
	if _, isBool := params["request"].(bool); !ok || isBool {
		return nil, badParam("request is a message number", "request")
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
		if r.set("settles") {
			if s, err := queryOne(e.DB, "SELECT * FROM requests WHERE id = ?", r.i("settles")); err != nil {
				return nil, err
			} else if s != nil {
				r = s
			}
		}
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
	ch := &chat{e: e}
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
	return map[string]any{"agent": chatAgent, "scope": scope, "message": message, "messages": page, "more_older": older, "more_newer": newer}, nil
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
	everyone, _, err := chatScopeParam(params, false)
	if err != nil {
		return nil, err
	}
	agent, err := e.chatAgent(params, false)
	if err != nil {
		return nil, err
	}
	like := "%" + strings.NewReplacer(`\`, `\\`, "%", `\%`, "_", `\_`).Replace(strings.TrimSpace(query_)) + "%"
	where, scopeArgs := "1 = 1", []any{}
	if agent != "" {
		where, scopeArgs = chatScope(agent, everyone)
	}
	hits, err := query(e.DB, "SELECT id FROM requests WHERE state NOT IN "+neverReached+" AND settles IS NULL AND "+where+
		` AND (body LIKE ? ESCAPE '\' OR EXISTS (SELECT 1 FROM requests a WHERE a.settles = requests.id AND a.body LIKE ? ESCAPE '\')) `+
		"ORDER BY id DESC LIMIT ?", append(scopeArgs, like, like, limit)...)
	if err != nil {
		return nil, err
	}
	ch := &chat{e: e}
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

func repliesOf(r row, agent string) []map[string]any {
	var out []map[string]any
	shown, hasShown := shownVerdict(r)
	if d, _, _ := dismissal(r); r.str("requester") == human && r.str("target") == agent && hasShown && shown != "" && r["answer_id"] != nil && !d {
		at := answeredAt(r)
		out = append(out, map[string]any{"id": itemID(num(at), itemReply, r.i("id")*2+1), "at": ts(at), "kind": "reply", "text": shown})
	}
	return out
}

// promptItemOf is a prompt block as a transcript item.
func promptItemOf(agent string, b row) map[string]any {
	return map[string]any{"id": itemID(b.i("first_seen"), itemPrompt, b.i("id")), "at": ts(b["first_seen"]), "kind": "prompt",
		"prompt": promptOf(agent, b), "open": !b.set("gone_at")}
}

// taskItemOf is one task event of the events table as a transcript item and the task it names; false when the chat does not show it.
func taskItemOf(ev row) (item map[string]any, task int64, ok bool) {
	event, known := transcriptEvents[ev.str("kind")]
	p := decodeObject(ev.str("payload"))
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
	return map[string]any{"id": itemID(ev.i("ts"), itemTask, ev.i("id")), "at": ts(ev["ts"]), "kind": "task_event",
		"task_event": map[string]any{"event": event, "task": tid, "request": p["request_id"], "evidence": evidence}}, tid, true
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
	ch := &chat{e: e}
	rs, _, err := ch.page(agent, false, nil, nil, perSource)
	if err != nil {
		return nil, err
	}
	var items []map[string]any
	for _, r := range rs {
		items = append(items, repliesOf(r, agent)...)
	}
	// A structured session's text blocks are its replies: each is a transcript event, and the event holds this item's id.
	texts, err := transcript.Replies(e.DB, agent, perSource)
	if err != nil {
		return nil, err
	}
	for _, t := range texts {
		items = append(items, map[string]any{"id": t.ID, "at": ts(t.At), "kind": "reply", "text": t.Text})
	}
	window := transcript.ToolWindow{Limit: limit + 1}
	if since != nil {
		window.Since = *since / 1_000_000
	} else if before != nil {
		window.Before = *before / 1_000_000
	}
	tools, err := transcript.Tools(e.DB, agent, window)
	if err != nil {
		return nil, err
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
	kinds := make([]string, 0, len(transcriptEvents))
	for k := range transcriptEvents {
		kinds = append(kinds, k)
	}
	sort.Strings(kinds)
	evs, err := query(e.DB, "SELECT id, ts, agent, kind, payload FROM events WHERE kind IN ("+placeholders(len(kinds))+") AND "+eventTask+
		" IN (SELECT id FROM tasks WHERE owner = ?) ORDER BY id DESC LIMIT ?", append(anySlice(kinds), agent, perSource)...)
	if err != nil {
		return nil, err
	}
	for _, ev := range evs {
		if item, _, ok := taskItemOf(ev); ok {
			items = append(items, item)
		}
	}
	sort.SliceStable(items, func(i, j int) bool { return items[i]["id"].(int64) < items[j]["id"].(int64) })
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
