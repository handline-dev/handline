package reads

import (
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

const (
	maxWaitS      = 600
	pollEvery     = 500 * time.Millisecond
	legacyDismiss = "read - no reply"
	cancelVerdict = "withdrawn by the founder"
)

// closingSQL and answerSQL: what a request closed with, as text: the say that answered it, else the reason it closed
// without one (db/requests.py CLOSING_SQL, ANSWER_SQL).
const (
	reasonEvent = "FROM events e WHERE e.kind = 'request_reason' AND " +
		"CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.request_id') END = requests.id"
	closingSQL = "COALESCE((SELECT a.body FROM requests a WHERE a.settles = requests.id ORDER BY a.id LIMIT 1), " +
		"(SELECT json_extract(e.payload, '$.reason') " + reasonEvent + " ORDER BY e.id DESC LIMIT 1))"
	answerSQL = closingSQL + " AS answer, (SELECT a.ts FROM requests a WHERE a.settles = requests.id ORDER BY a.id LIMIT 1) AS answered_at"

	dismissedEvent = "FROM events e WHERE e.kind = 'dismissed' AND " +
		"CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.request_id') END = requests.id"
	dismissedSQL = "(SELECT json_extract(e.payload, '$.by') " + dismissedEvent + " ORDER BY e.id DESC LIMIT 1) AS dismisser, " +
		"(SELECT json_extract(e.payload, '$.reason') " + dismissedEvent + " ORDER BY e.id DESC LIMIT 1) AS dismiss_reason"
)

// shownVerdict is what a screen may print as a request's answer (db.shown_verdict).
func shownVerdict(r row) (string, bool) {
	text, has := r["answer"].(string)
	by, reason := r.str("dismisser"), r.str("dismiss_reason")
	switch {
	case by != "":
	case has && text == legacyDismiss:
		by, reason = "", ""
	case has && strings.HasPrefix(text, legacyDismiss+": "):
		by, reason = "", text[len(legacyDismiss)+2:]
	default:
		return text, has
	}
	out := "dismissed"
	if by != "" {
		out += " by " + by
	}
	if reason != "" {
		out += " - " + reason
	}
	return out, true
}

// mailbox is one row of the founder's mailbox: a request that waits for them, or an answer to one of theirs.
type mailbox struct {
	id        int64
	ts        int64
	from      string
	kind      string
	body      string
	seq       int64
	gate, ans bool
}

func (e *Env) mailboxRows() ([]mailbox, error) {
	gateRows, err := query(e.DB, "SELECT id FROM requests WHERE target = ? AND state = 'queued' AND id IN "+
		"(SELECT MIN(l.request_id) FROM task_requests l JOIN tasks t ON t.id = l.task_id "+
		"WHERE t.owner = ? AND t.discarded_by IS NULL AND t.done_evidence IS NULL GROUP BY t.id) ORDER BY id", human, human)
	if err != nil {
		return nil, err
	}
	gates := map[int64]bool{}
	for _, g := range gateRows {
		gates[g.i("id")] = true
	}
	rows, err := query(e.DB, "SELECT id, ts, requester, kind, body FROM requests WHERE target = ? AND (state = 'queued' OR "+
		"(kind = 'say' AND state = 'landed' AND landed_at IS NOT NULL AND read_at IS NULL AND settles IS NULL)) ORDER BY id", human)
	if err != nil {
		return nil, err
	}
	var out []mailbox
	for _, r := range rows {
		out = append(out, mailbox{id: r.i("id"), ts: r.i("ts"), from: r.str("requester"), kind: r.str("kind"),
			body: r.str("body"), seq: r.i("id"), gate: gates[r.i("id")]})
	}
	answers, err := query(e.DB, "SELECT id, ts, state_at, target, kind, state, body, parent_id, chain_id, "+dismissedSQL+", "+answerSQL+
		" FROM requests WHERE requester = ? AND state IN ('landed', 'withdrawn') AND EXISTS (SELECT 1 FROM requests a "+
		"WHERE a.settles = requests.id AND a.state = 'landed' AND a.delivered_at IS NULL) ORDER BY id", human)
	if err != nil {
		return nil, err
	}
	for _, r := range answers {
		verdict, has := shownVerdict(r)
		kind := "answer"
		if r.str("state") == "withdrawn" || (has && verdict == cancelVerdict) {
			kind = "refused"
		}
		at := r["answered_at"]
		if r.i("answered_at") == 0 {
			at = r["state_at"]
			if r.i("state_at") == 0 {
				at = r["ts"]
			}
		}
		if !has || verdict == "" {
			verdict = "(answered with nothing written)"
		}
		seq := r.i("id")
		if a, err := queryOne(e.DB, "SELECT id FROM requests WHERE settles = ? ORDER BY id LIMIT 1", r.i("id")); err != nil {
			return nil, err
		} else if a != nil {
			seq = a.i("id")
		}
		out = append(out, mailbox{id: r.i("id"), ts: num(at), from: r.str("target"), kind: kind, body: verdict, seq: seq, ans: true})
	}
	return out, nil
}

// firstLine is the first line of text that has anything in it, trimmed.
func firstLine(text string) string {
	for _, ln := range strings.Split(strings.NewReplacer("\r\n", "\n", "\r", "\n").Replace(text), "\n") {
		if t := strings.TrimSpace(ln); t != "" {
			return t
		}
	}
	return "(empty)"
}

func (e *Env) item(id int64, kind, agent, body string, at int64, read, answered bool) (map[string]any, error) {
	tasks, err := query(e.DB, "SELECT t.* FROM tasks t JOIN task_requests l ON l.task_id = t.id WHERE l.request_id = ? ORDER BY t.id", id)
	if err != nil {
		return nil, err
	}
	var evidence, task any
	if len(tasks) > 0 {
		evidence, task = tasks[0].strOrNil("done_evidence"), tasks[0].i("id")
	}
	return map[string]any{"id": id, "kind": kind, "agent": agent, "title": head(firstLine(body), 200), "body": body,
		"evidence": evidence, "task": task, "created": ts(at), "read": read, "answered": answered,
		"options": []any{}, "prompt": nil}, nil
}

// founderItems is the mailbox the screen draws.
func (e *Env) founderItems() ([]map[string]any, error) { return e.FounderItems(0) }

// FounderItems is the mailbox from request number since up (0 is all of it): what inbox.list shows the founder, item by item.
func (e *Env) FounderItems(since int64) ([]map[string]any, error) {
	rows, err := e.mailboxRows()
	if err != nil {
		return nil, err
	}
	seen := map[string]int64{}
	var out []map[string]any
	for _, m := range rows {
		if m.id < since {
			continue
		}
		cursor, ok := seen[m.from]
		if !ok {
			cr, err := queryOne(e.DB, "SELECT row_id FROM cursors WHERE name = ?", "for_you_seen:"+m.from)
			if err != nil {
				return nil, err
			}
			cursor = cr.i("row_id")
			seen[m.from] = cursor
		}
		kind := m.kind
		if m.gate {
			kind = "decision"
		} else if kind == "" {
			kind = "ask"
		}
		it, err := e.item(m.id, kind, m.from, m.body, m.ts, m.seq <= cursor, false)
		if err != nil {
			return nil, err
		}
		out = append(out, it)
	}
	return out, nil
}

// agentItems is an agent's own incoming requests.
func (e *Env) agentItems(agent string) ([]map[string]any, error) { return e.AgentItems(agent, 0) }

// AgentItems is an agent's own incoming requests: all of them, or with only set the one request of that number.
func (e *Env) AgentItems(agent string, only int64) ([]map[string]any, error) {
	which, args := "", []any{agent}
	if only != 0 {
		which, args = "AND id = ? ", append(args, only)
	}
	rows, err := query(e.DB, "SELECT id, ts, requester, kind, body, read_at, state, "+answerSQL+" FROM requests "+
		"WHERE target = ? "+which+"AND edge_ok AND COALESCE(notice, 0) = 0 ORDER BY id", args...)
	if err != nil {
		return nil, err
	}
	var out []map[string]any
	for _, r := range rows {
		kind := r.str("kind")
		if kind == "" {
			kind = "ask"
		}
		it, err := e.item(r.i("id"), kind, r.str("requester"), r.str("body"), r.i("ts"),
			r.set("read_at") || r.str("state") != "queued", r.set("answer"))
		if err != nil {
			return nil, err
		}
		out = append(out, it)
	}
	return out, nil
}

// Answered is whether the request has been answered, by the rule an agent's item gives: something closed it with words.
func (e *Env) Answered(id int64) (bool, error) {
	r, err := queryOne(e.DB, "SELECT "+answerSQL+" FROM requests WHERE id = ?", id)
	if err != nil || r == nil {
		return false, err
	}
	return r.set("answer"), nil
}

func (e *Env) inboxPage(c *rpc.Conn, params map[string]any) (map[string]any, error) {
	unread, err := optBool(params, "unread")
	if err != nil {
		return nil, err
	}
	kind, err := optStr(params, "kind")
	if err != nil {
		return nil, err
	}
	agent, err := optStr(params, "agent")
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
	var items []map[string]any
	if c.Caller.Role == "agent" {
		items, err = e.agentItems(c.Caller.Info["name"].(string))
	} else {
		items, err = e.founderItems()
	}
	if err != nil {
		return nil, err
	}
	sort.SliceStable(items, func(i, j int) bool { return items[i]["id"].(int64) > items[j]["id"].(int64) }) // newest first
	var kept []map[string]any
	for _, it := range items {
		if (unread && it["read"].(bool)) || (kind != nil && it["kind"] != *kind) || (agent != nil && it["agent"] != *agent) ||
			(before != nil && it["id"].(int64) >= *before) {
			continue
		}
		kept = append(kept, it)
	}
	page := []any{}
	for i, it := range kept {
		if i >= limit {
			break
		}
		page = append(page, it)
	}
	return map[string]any{"items": page, "more": len(kept) > limit}, nil
}

// inboxList holds the call while nothing matches, up to wait_s, and ends when the client hangs up.
func (e *Env) inboxList(c *rpc.Conn, params map[string]any) (any, error) {
	wait, err := optInt(params, "wait_s")
	if err != nil || (wait != nil && (*wait < 0 || *wait > maxWaitS)) {
		return nil, badParam("wait_s is an integer from 0 to 600", "wait_s")
	}
	first, err := e.inboxPage(c, params)
	if err != nil || wait == nil || *wait == 0 || len(first["items"].([]any)) > 0 {
		return first, err
	}
	until := time.NewTimer(time.Duration(*wait) * time.Second)
	defer until.Stop()
	tick := time.NewTicker(pollEvery)
	defer tick.Stop()
	for {
		select {
		case <-c.Done():
			return first, nil
		case <-until.C:
			return first, nil
		case <-tick.C:
			page, err := e.inboxPage(c, params)
			if err != nil || len(page["items"].([]any)) > 0 {
				return page, err
			}
		}
	}
}
