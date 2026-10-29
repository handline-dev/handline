package reads

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// for_you.list: every open item waiting on the founder, whatever it is made of, so a badge, a push and Later share one set.

func (e *Env) forYouList(_ *rpc.Conn, _ map[string]any) (any, error) {
	items, count, err := e.ForYou()
	if err != nil {
		return nil, err
	}
	out := make([]any, len(items))
	for i, it := range items {
		out[i] = it
	}
	return map[string]any{"items": out, "count": count}, nil
}

func forYouItem(kind, id string, agent any, title, asks string, since any) map[string]any {
	return map[string]any{"kind": kind, "id": id, "agent": agent, "title": title, "asks": asks, "since": since}
}

// ForYou is the open items waiting on the founder, oldest first, and the count of those not deferred.
func (e *Env) ForYou() ([]map[string]any, int, error) {
	var items []map[string]any
	have := map[string]bool{}
	add := func(it map[string]any) {
		key := it["kind"].(string) + "\x00" + it["id"].(string)
		if !have[key] {
			have[key] = true
			items = append(items, it)
		}
	}
	asks := func(task int64) (string, error) {
		r, err := queryOne(e.DB, "SELECT 1 AS n FROM task_tags WHERE task_id = ? AND tag = 'review'", task)
		if err != nil || r != nil {
			return "review", err
		}
		return "decide", nil
	}

	// Asks to the founder, one item for a task whatever number of requests are open on it. A say asks nothing.
	mail, err := e.mailboxRowsKept(false)
	if err != nil {
		return nil, 0, err
	}
	for _, m := range mail {
		if m.kind == "say" {
			continue
		}
		linked, err := queryOne(e.DB, "SELECT t.id, t.title FROM tasks t JOIN task_requests l ON l.task_id = t.id WHERE l.request_id = ? ORDER BY t.id LIMIT 1", m.id)
		if err != nil {
			return nil, 0, err
		}
		kind, id, title, task := "comment", m.ref, firstLine(withoutHumanTag(m.body)), int64(0)
		if linked != nil {
			task = linked.i("id")
		}
		switch {
		case m.memo != 0:
			id = strconv.FormatInt(m.memo, 10)
		case task != 0:
			kind, id, title = "task", strconv.FormatInt(task, 10), linked.str("title")
		}
		ask := "decide"
		if task != 0 {
			if ask, err = asks(task); err != nil {
				return nil, 0, err
			}
		}
		add(forYouItem(kind, id, m.from, title, ask, ts(m.ts)))
	}

	// Tasks given to the founder that no ask above already stands for.
	b, err := e.loadBoard()
	if err != nil {
		return nil, 0, err
	}
	owned, err := query(e.DB, "SELECT * FROM tasks WHERE owner = ? AND "+openTask+" ORDER BY id", human)
	if err != nil {
		return nil, 0, err
	}
	for _, t := range owned {
		if s := b.state(t); s != "queued" && s != "doing" {
			continue
		}
		ask, err := asks(t.i("id"))
		if err != nil {
			return nil, 0, err
		}
		from := t.str("reporter")
		if from == "" {
			from = t.str("opened_by")
		}
		add(forYouItem("task", strconv.FormatInt(t.i("id"), 10), from, t.str("title"), ask, ts(t["created_at"])))
	}

	// The prompts agents wait on, while the screen watch still sees them.
	prompts, err := query(e.DB, "SELECT * FROM prompt_blocks WHERE gone_at IS NULL AND last_seen >= ? ORDER BY id", time.Now().Unix()-promptStaleS)
	if err != nil {
		return nil, 0, err
	}
	for _, r := range prompts {
		p := promptOf(r.str("agent"), r)
		title := "needs your answer"
		for _, k := range []string{"command", "tool", "reason"} {
			if s, _ := p[k].(string); s != "" {
				title = s
				break
			}
		}
		ask := "decide"
		if p["kind"] == "permission" {
			ask = "allow"
		}
		add(forYouItem("prompt", p["id"].(string), p["agent"], title, ask, p["since"]))
	}

	// HR proposals that wait for the founder (a client counts the dot from open and apply_failed, never applying).
	for _, p := range e.proposals() {
		if p.state == "open" || p.state == "apply_failed" {
			v := e.proposalView(p)
			add(forYouItem("hr", fmt.Sprint(p.wireID), v["agent"], p.title(), "allow", ts(p.created)))
		}
	}

	// What the machine needs.
	needs, err := e.machineNeeds()
	if err != nil {
		return nil, 0, err
	}
	for _, n := range needs {
		need := n.(map[string]any)
		add(forYouItem("machine", need["kind"].(string), nil, need["text"].(string), "do", need["since"]))
	}

	count := 0
	for _, it := range items {
		later, err := e.laterOfID(it["kind"].(string), it["id"].(string))
		if err != nil {
			return nil, 0, err
		}
		if later != nil {
			it["later"] = later
		} else {
			count++
		}
	}
	sort.SliceStable(items, func(i, j int) bool {
		a, b := items[i], items[j]
		if as, bs := fmt.Sprint(a["since"]), fmt.Sprint(b["since"]); as != bs {
			return as < bs
		}
		if a["kind"] != b["kind"] {
			return a["kind"].(string) < b["kind"].(string)
		}
		return a["id"].(string) < b["id"].(string)
	})
	return items, count, nil
}

// withoutHumanTag is a comment's ask without the mention of the founder that raised it.
func withoutHumanTag(text string) string { return strings.ReplaceAll(text, "@human", "") }
