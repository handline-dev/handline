package reads

import (
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

const (
	maxWaitS  = 600
	pollEvery = 500 * time.Millisecond
)

// reasonSQL is what a request closed with, as text, read as its answer (db/requests.go reasonSQL): the last reason recorded for it.
const reasonSQL = "(SELECT json_extract(e.payload, '$.reason') FROM events e WHERE e.kind = 'request_reason' AND " +
	"CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.request_id') END = requests.id ORDER BY e.id DESC LIMIT 1) AS answer"

// shownVerdict is what a screen may print as a request's answer (db.shown_verdict).
func shownVerdict(r row) (string, bool) {
	text, has := r["answer"].(string)
	return text, has
}

// mailbox is one row of the founder's mailbox: a request that waits for them. gate is a decision: the card of a task the founder owns.
type mailbox struct {
	id   int64
	ts   int64
	ref  string
	from string
	kind string
	body string
	gate bool
	// kept is a say already read, still listed for readSayKeepS.
	kept bool
}

// founderOpen is a request to the founder still in their inbox: not yet dealt with, or a say not yet read.
const founderOpen = "(state = 'queued' OR (kind = 'say' AND state = 'landed' AND landed_at IS NOT NULL AND read_at IS NULL))"

// readSayKeepS is how long a read say stays in the founder's inbox list, greyed, so it can be found again: 7 days.
const readSayKeepS = 7 * 24 * 3600

func (e *Env) mailboxRows() ([]mailbox, error) { return e.mailboxRowsKept(false) }

// mailboxRowsKept is the mailbox; with kept it also holds the says read within readSayKeepS.
func (e *Env) mailboxRowsKept(kept bool) ([]mailbox, error) {
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
	where, args := founderOpen, []any{human}
	if kept {
		where = "(" + founderOpen + " OR (kind = 'say' AND state = 'landed' AND read_at IS NOT NULL AND read_at >= ?))"
		args = append(args, time.Now().Unix()-readSayKeepS)
	}
	rows, err := query(e.DB, "SELECT id, ref, ts, requester, kind, body, read_at FROM requests WHERE target = ? AND "+where+" ORDER BY id", args...)
	if err != nil {
		return nil, err
	}
	var out []mailbox
	for _, r := range rows {
		out = append(out, mailbox{id: r.i("id"), ref: r.str("ref"), ts: r.i("ts"), from: r.str("requester"), kind: r.str("kind"),
			body: r.str("body"), gate: gates[r.i("id")], kept: r.i("read_at") != 0})
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

func (e *Env) item(id int64, ref, kind, agent, body string, at int64, read bool) (map[string]any, error) {
	tasks, err := query(e.DB, "SELECT t.* FROM tasks t JOIN task_requests l ON l.task_id = t.id WHERE l.request_id = ? ORDER BY t.id", id)
	if err != nil {
		return nil, err
	}
	var evidence, task any
	if len(tasks) > 0 {
		evidence, task = tasks[0].strOrNil("done_evidence"), tasks[0].i("id")
	}
	// fix is the action a card raised by the daemon's own healing offers as a button (heal.FixRestartTerminal).
	fix, err := queryOne(e.DB, "SELECT json_extract(payload, '$.fix') AS fix FROM events WHERE kind = 'raise_decision' AND "+
		"CASE WHEN json_valid(payload) THEN json_extract(payload, '$.request_id') END = ? ORDER BY id DESC LIMIT 1", id)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"id": id, "ref": ref, "kind": kind, "agent": agent, "title": head(firstLine(body), 200), "body": body,
		"evidence": evidence, "task": task, "created": ts(at), "read": read, "prompt": nil}
	if fix.set("fix") {
		out["fix"] = fix["fix"]
	}
	return out, nil
}

// founderItems is the mailbox the screen draws.
func (e *Env) founderItems() ([]map[string]any, error) { return e.FounderItems(0) }

// FounderItems is the mailbox from request number since up (0 is all of it): what inbox.list shows the founder, item by item.
func (e *Env) FounderItems(since int64) ([]map[string]any, error) {
	return e.founderItemsKept(since, false)
}

// founderItemsKept is FounderItems; with kept it also lists the says read in the last 7 days, as read.
func (e *Env) founderItemsKept(since int64, kept bool) ([]map[string]any, error) {
	rows, err := e.mailboxRowsKept(kept)
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
		it, err := e.item(m.id, m.ref, kind, m.from, m.body, m.ts, m.id <= cursor || (m.kept && !m.gate))
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
	rows, err := query(e.DB, "SELECT id, ref, ts, requester, kind, body, read_at, state FROM requests "+
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
		it, err := e.item(r.i("id"), r.str("ref"), kind, r.str("requester"), r.str("body"), r.i("ts"),
			r.set("read_at") || r.str("state") != "queued")
		if err != nil {
			return nil, err
		}
		out = append(out, it)
	}
	return out, nil
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
		items, err = e.founderItemsKept(0, true)
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
