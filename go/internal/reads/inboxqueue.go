package reads

import (
	"sort"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
)

const (
	lineShown  = 60 // characters of a request that a listing row prints
	dealtShown = 20
)

// inboxQueue is inbox.queue (hl inbox): whose inbox it is and what the command prints of it, cmd_inbox in handline/cli/ask.py. An
// agent's is the head of its queue, with its whole history under all; the founder's is the mailbox folded to a line per agent, with
// what was already dealt with under all. With wait_s the call holds until something newer than since waits there.
func (e *Env) inboxQueue(c *rpc.Conn, params map[string]any) (any, error) {
	caller := human
	if c.Caller.Role == "agent" {
		caller = c.Caller.Info["name"].(string)
	}
	whose := caller
	if w, err := optStr(params, "whose"); err != nil {
		return nil, err
	} else if w != nil {
		whose = *w
	}
	all, err := optBool(params, "all")
	if err != nil {
		return nil, err
	}
	var since int64
	if s, err := optInt(params, "since"); err != nil {
		return nil, err
	} else if s != nil {
		since = *s
	}
	wait, err := optInt(params, "wait_s")
	if err != nil || (wait != nil && (*wait < 0 || *wait > maxWaitS)) {
		return nil, badParam("wait_s is an integer from 0 to 600", "wait_s")
	}
	if why := e.listingRefusal(caller, whose); why != "" {
		return nil, rpc.Refused(why, "listing", "")
	}
	var until time.Time
	if wait != nil {
		until = time.Now().Add(time.Duration(*wait) * time.Second)
	}
	for {
		newest, err := e.newestWaiting(whose)
		if err != nil {
			return nil, err
		}
		held := newest <= since && wait != nil && time.Now().Before(until)
		if held {
			select {
			case <-c.Done():
				held = false
			case <-time.After(pollEvery):
			}
		}
		if !held && whose == human {
			return e.founderQueue(all)
		} else if !held {
			return e.agentQueue(whose, newest, all)
		}
	}
}

// listingRefusal is actions.listing_refusal: why caller may not list whose messages, "" when it may. The founder, the agent itself
// and its supervisor may.
func (e *Env) listingRefusal(caller, whose string) string {
	if caller == human || caller == whose {
		return ""
	}
	if t, _ := team.LoadTeam(e.Home); t.SupervisorOf(whose) == caller {
		return ""
	}
	return caller + " may not list " + whose + "'s messages: only " + whose + ", its supervisor and the human may. " +
		"`hl read <id>` shows one request by number."
}

// newestWaiting is the number of the newest thing that waits in whose inbox, 0 for nothing: for the founder a mailbox row (an answer
// counts by its own number, not the request's), for an agent a request no state has settled.
func (e *Env) newestWaiting(whose string) (int64, error) {
	if whose != human {
		r, err := queryOne(e.DB, "SELECT COALESCE(MAX(id), 0) AS newest FROM requests WHERE target = ? AND edge_ok AND state NOT IN "+
			"('landed', 'denied', 'withdrawn', 'replaced')", whose)
		return r.i("newest"), err
	}
	rows, err := e.mailboxRows()
	var newest int64
	for _, m := range rows {
		newest = max(newest, m.seq)
	}
	return newest, err
}

func (e *Env) agentQueue(whose string, newest int64, all bool) (any, error) {
	out := map[string]any{"whose": whose, "newest": newest, "head": nil}
	first, err := db.HeadOfQueue(e.DB, whose, 0)
	if err != nil {
		return nil, err
	}
	if first != nil {
		depth, err := db.QueueDepth(e.DB, whose)
		if err != nil {
			return nil, err
		}
		closes := "`hl say " + first.Requester + " --parent <id> \"<answer>\"` answers it"
		if first.Kind.String == "task" {
			if closes, err = supervisor.AnswerVerb(e.DB, first); err != nil {
				return nil, err
			}
		}
		out["head"] = map[string]any{"id": first.ID, "requester": first.Requester, "chain_depth": first.ChainDepth, "body": first.Body,
			"more_waiting": depth - 1, "more_waiting_line": supervisor.MoreWaiting(first.Kind.String, depth-1), "how_to_close": closes}
	}
	if all {
		rows, err := query(e.DB, "SELECT id, requester, state, chain_depth, body FROM requests WHERE target = ? AND edge_ok ORDER BY id", whose)
		if err != nil {
			return nil, err
		}
		history := []any{}
		for _, r := range rows {
			history = append(history, map[string]any{"id": r.i("id"), "requester": r.str("requester"), "state": r.str("state"),
				"chain_depth": r.i("chain_depth"), "line": head(oneline(r.str("body")), lineShown)})
		}
		out["history"] = history
	}
	return out, nil
}

// founderQueue is the founder's mailbox as `hl inbox` lists it: actions.messages, a line per agent that stands for everything it sent
// and the founder has not seen, a decision or a request parked on a reply keeping its own line.
func (e *Env) founderQueue(all bool) (any, error) {
	rows, err := e.mailboxRows()
	if err != nil {
		return nil, err
	}
	type line struct {
		mailbox
		unread int
	}
	var lines []line
	folded := map[string]int{} // sender -> its folded line
	var newest int64
	for _, m := range rows {
		newest = max(newest, m.seq)
		cr, err := queryOne(e.DB, "SELECT row_id FROM cursors WHERE name = ?", "for_you_seen:"+m.from)
		if err != nil {
			return nil, err
		}
		if m.seq <= cr.i("row_id") {
			continue
		}
		at, has := folded[m.from]
		switch {
		case m.gate || m.waiting:
			lines = append(lines, line{m, 1})
		case !has:
			folded[m.from] = len(lines)
			lines = append(lines, line{m, 1})
		default:
			if l := lines[at]; m.ts > l.ts || (m.ts == l.ts && m.id > l.id) {
				lines[at].mailbox = m
			}
			lines[at].unread++
		}
	}
	sort.SliceStable(lines, func(i, j int) bool {
		a, b := lines[i], lines[j]
		switch {
		case a.gate != b.gate:
			return a.gate
		case a.waiting != b.waiting:
			return a.waiting
		case a.ts != b.ts:
			return a.ts < b.ts
		}
		return a.id < b.id
	})
	shown := []any{}
	listed := map[int64]bool{}
	for _, l := range lines {
		kind := l.kind
		if kind == "" {
			kind = "ask"
		}
		listed[l.id] = true
		shown = append(shown, map[string]any{"id": l.id, "from": l.from, "kind": kind, "decision": l.gate, "answer": l.ans,
			"line": head(oneline(firstLine(l.body)), lineShown), "unread": l.unread})
	}
	out := map[string]any{"whose": human, "newest": newest, "head": nil, "waiting": len(rows), "lines": shown}
	if !all {
		return out, nil
	}
	done, err := query(e.DB, "SELECT id, requester, target, state, body, "+dismissedSQL+", "+answerSQL+" FROM requests "+
		"WHERE (target = ? OR requester = ?) AND state != 'queued' ORDER BY id DESC LIMIT ?", human, human, dealtShown)
	if err != nil {
		return nil, err
	}
	dealt := []any{}
	for _, r := range done {
		if listed[r.i("id")] {
			continue // still on the list above, so not dealt with
		}
		outgoing := r.str("requester") == human
		other, text := r.str("requester"), r.str("body")
		if outgoing {
			other = r.str("target")
		}
		if verdict, has := shownVerdict(r); has && verdict != "" {
			text = verdict
		}
		dealt = append(dealt, map[string]any{"id": r.i("id"), "outgoing": outgoing, "agent": other, "state": r.str("state"),
			"line": head(oneline(firstLine(text)), lineShown)})
	}
	out["dealt"] = dealt
	return out, nil
}
