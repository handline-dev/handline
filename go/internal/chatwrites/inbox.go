package chatwrites

import (
	"database/sql"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// inbox.mark_read, inbox.answer and inbox.reply (handline/daemon/rpc_inbox.py): the writes beside inbox.list.

const maxIDs = 200

// mailRow is one row of the person's mailbox as the writes need it (actions.mailbox_rows): who it is from, what a chat's mark is compared
// with, and whether it is a decision or a reply owed.
type mailRow struct {
	id, seq       int64
	from          string
	gate, waiting bool
}

func mailbox(q db.Q) ([]mailRow, error) {
	gates, err := db.OpenDecisionCardIDs(q, "", "", nil)
	if err != nil {
		return nil, err
	}
	isGate := map[int64]bool{}
	for _, g := range gates {
		isGate[g] = true
	}
	rows, err := q.Query("SELECT id, requester, kind, response FROM requests WHERE target = ? AND (state = 'queued' OR "+
		"(kind = 'say' AND state = 'landed' AND landed_at IS NOT NULL AND read_at IS NULL AND settles IS NULL)) ORDER BY id", db.Human)
	if err != nil {
		return nil, err
	}
	var out []mailRow
	for rows.Next() {
		var id int64
		var requester string
		var kind, response sql.NullString
		if err := rows.Scan(&id, &requester, &kind, &response); err != nil {
			rows.Close()
			return nil, err
		}
		out = append(out, mailRow{id: id, seq: id, from: requester, gate: isGate[id], waiting: response.String == "async" && kind.String != "say"})
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	answers, err := db.UnannouncedAnswers(q, db.Human)
	if err != nil {
		return nil, err
	}
	for _, r := range answers {
		seq := r.ID
		if a, err := db.AnswerRowOf(q, r.ID); err != nil {
			return nil, err
		} else if a != nil {
			seq = a.ID
		}
		out = append(out, mailRow{id: r.ID, seq: seq, from: r.Target})
	}
	return out, nil
}

func itemParam(p map[string]any, key string) (int64, error) {
	n, ok := rpc.AsInt(p[key])
	if !ok {
		return 0, badParam(key+" is an item id", key)
	}
	return n, nil
}

func noItem(i int64) *rpc.Error {
	return notFound("item", strconv.FormatInt(i, 10), fmt.Sprintf("no item %d in this inbox", i))
}

func idList(p map[string]any) ([]int64, error) {
	list, ok := p["ids"].([]any)
	bad := badParam(fmt.Sprintf("ids is a list of 1 to %d item ids", maxIDs), "ids")
	if !ok || len(list) == 0 || len(list) > maxIDs {
		return nil, bad
	}
	seen := map[int64]bool{}
	var ids []int64
	for _, v := range list {
		n, ok := rpc.AsInt(v)
		if !ok {
			return nil, bad
		}
		if !seen[n] {
			seen[n] = true
			ids = append(ids, n)
		}
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids, nil
}

// aMailItem is a request an agent's inbox shows: addressed to it, its edge allowed, and no notice.
func aMailItem(r *db.Request, me string) bool {
	return r != nil && r.Target == me && r.EdgeOK.Int64 != 0 && r.Notice.Int64 == 0
}

type seenMark struct {
	agent string
	upto  int64
}

func (w *writes) markRead(c *rpc.Conn, p map[string]any) (any, error) {
	ids, err := idList(p)
	if err != nil {
		return nil, err
	}
	me := c.Caller.Actor()
	agent := c.Caller.Role == "agent"
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		var said string
		var reads []seenMark
		if agent {
			for _, i := range ids {
				row, err := db.GetRequest(tx, i)
				if err != nil {
					return nil, err
				}
				if !aMailItem(row, me) {
					return nil, noItem(i)
				}
			}
			head, err := db.HeadOfQueue(tx, me, 0)
			if err != nil {
				return nil, err
			}
			if len(ids) != 1 || head == nil || head.ID != ids[0] {
				return nil, rpc.Refused("an agent reads its queue in order: only the item `hl inbox` shows can be read", "state", "")
			}
			said = fmt.Sprintf("item %d read", ids[0])
		} else {
			rows, err := mailbox(tx)
			if err != nil {
				return nil, err
			}
			box := map[int64]mailRow{}
			for _, m := range rows {
				box[m.id] = m
			}
			for _, i := range ids {
				if _, in := box[i]; in {
					continue
				}
				row, err := db.GetRequest(tx, i)
				if err != nil {
					return nil, err
				}
				if row == nil || row.Target != db.Human {
					return nil, noItem(i) // one the person already read has left the mailbox: reading it again is nothing to do
				}
			}
			unseen := map[int64]mailRow{}
			byAgent := map[string][]mailRow{}
			for _, m := range rows {
				cur, err := db.Cursor(tx, "for_you_seen:"+m.from)
				if err != nil {
					return nil, err
				}
				if m.seq > cur {
					unseen[m.id] = m
					byAgent[m.from] = append(byAgent[m.from], m)
				}
			}
			named := map[string]bool{}
			var inBox []int64
			for _, i := range ids {
				if m, in := box[i]; in {
					inBox = append(inBox, i)
					named[m.from] = true
				}
			}
			agents := make([]string, 0, len(named))
			for a := range named {
				agents = append(agents, a)
			}
			sort.Strings(agents)
			read := 0
			for _, a := range agents {
				var upto int64
				for _, i := range inBox {
					if box[i].from == a && box[i].seq > upto {
						upto = box[i].seq
					}
				}
				owed := int64(0)
				for _, m := range byAgent[a] {
					if (m.gate || m.waiting) && (owed == 0 || m.seq < owed) {
						owed = m.seq
					}
				}
				var top int64
				n := 0
				for _, m := range byAgent[a] {
					if m.seq <= upto && !(m.gate || m.waiting) && (owed == 0 || m.seq < owed) {
						n++
						if m.seq > top {
							top = m.seq
						}
					}
				}
				if n > 0 {
					reads = append(reads, seenMark{a, top})
					read += n
				}
			}
			if read == 0 {
				for _, i := range inBox {
					if _, in := unseen[i]; in {
						return nil, rpc.Refused("a decision or a reply owed is answered, never read away", "state", "")
					}
				}
			}
			said = fmt.Sprintf("%d read", read)
		}
		if agent {
			if _, err := db.MarkDelivered(tx, ids[0]); err != nil {
				return nil, err
			}
			if err := db.MarkLanded(tx, ids[0]); err != nil {
				return nil, err
			}
		}
		for _, m := range reads {
			if err := ForYouSeen(tx, m.agent, m.upto); err != nil {
				return nil, err
			}
		}
		return map[string]any{"accepted": true, "said": said}, nil
	})
}

func (w *writes) inboxAnswer(c *rpc.Conn, p map[string]any) (any, error) {
	rid, err := itemParam(p, "id")
	if err != nil {
		return nil, err
	}
	option, text := p["option"], p["text"]
	if (option == nil) == (text == nil) {
		return nil, badParam("an answer is an option or text, one of them", "text")
	}
	if option != nil {
		return nil, badParam("no item offers options yet: answer with text", "option")
	}
	body, err := inboxText(p, "text")
	if err != nil {
		return nil, err
	}
	gone := func(r *Result) error {
		if strings.HasSuffix(r.Msg, "is gone") {
			return noItem(rid)
		}
		return refuse(r)
	}
	return w.writeResult(c, gone, func(tx *sql.Tx) (*Result, map[string]any, error) {
		rows, err := mailbox(tx)
		if err != nil {
			return nil, nil, err
		}
		in := false
		for _, m := range rows {
			in = in || m.id == rid
		}
		if !in {
			return nil, nil, noItem(rid)
		}
		res, err := AnswerWrite(w.e, w.tell, tx, rid, body, nil)
		return res, nil, err
	})
}

func inboxText(p map[string]any, key string) (string, error) { return text(p, key) }

func (w *writes) inboxReply(c *rpc.Conn, p map[string]any) (any, error) {
	rid, err := itemParam(p, "id")
	if err != nil {
		return nil, err
	}
	body, err := text(p, "text")
	if err != nil {
		return nil, err
	}
	me := c.Caller.Actor()
	return w.writeResult(c, refuse, func(tx *sql.Tx) (*Result, map[string]any, error) {
		row, err := db.GetRequest(tx, rid)
		if err != nil {
			return nil, nil, err
		}
		var target string
		if c.Caller.Role == "agent" {
			if !aMailItem(row, me) {
				return nil, nil, noItem(rid)
			}
			target = row.Requester
		} else {
			rows, err := mailbox(tx)
			if err != nil {
				return nil, nil, err
			}
			found := false
			for _, m := range rows {
				if m.id == rid {
					found, target = true, m.from
				}
			}
			if !found || row == nil {
				return nil, nil, noItem(rid)
			}
			if row.State == "queued" && row.Target == db.Human {
				tid, err := db.DecisionTaskOfCard(tx, rid)
				if err != nil {
					return nil, nil, err
				}
				if tid != 0 {
					return nil, nil, rpc.Refused("a decision is answered with inbox.answer, not replied to", "state", "")
				}
			}
		}
		res, err := SayWrite(w.e, tx, me, target, body, rid, nil)
		return res, nil, err
	})
}
