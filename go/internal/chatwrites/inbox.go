package chatwrites

import (
	"database/sql"
	"fmt"
	"sort"
	"strconv"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/requestref"
	"github.com/handline/handline/go/internal/rpc"
)

// inbox.mark_read and inbox.reply (handline/daemon/rpc_inbox.py): the writes beside inbox.list.

const maxIDs = 200

// mailRow is one row of the person's mailbox as the writes need it (actions.mailbox_rows): who it is from and whether it is a decision.
type mailRow struct {
	id   int64
	from string
	gate bool
	memo bool // an ask a task comment raised (requests.memo_id): read away only by naming it
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
	rows, err := q.Query("SELECT id, requester, memo_id IS NOT NULL FROM requests WHERE target = ? AND (state = 'queued' OR "+
		"(kind = 'say' AND state = 'landed' AND landed_at IS NOT NULL AND read_at IS NULL)) ORDER BY id", db.Human)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []mailRow
	for rows.Next() {
		var id int64
		var requester string
		var memo bool
		if err := rows.Scan(&id, &requester, &memo); err != nil {
			return nil, err
		}
		out = append(out, mailRow{id: id, from: requester, gate: isGate[id], memo: memo})
	}
	return out, rows.Err()
}

func noItem(i int64) *rpc.Error {
	return notFound("item", strconv.FormatInt(i, 10), fmt.Sprintf("no item %d in this inbox", i))
}

func noItemNamed(name string) error { return notFound("item", name, "no item "+name+" in this inbox") }

func (w *writes) idList(c *rpc.Conn, p map[string]any) ([]int64, error) {
	list, ok := p["ids"].([]any)
	bad := badParam(fmt.Sprintf("ids is a list of 1 to %d item ids", maxIDs), "ids")
	if !ok || len(list) == 0 || len(list) > maxIDs {
		return nil, bad
	}
	seen := map[int64]bool{}
	var ids []int64
	for _, v := range list {
		n, err := requestref.Resolve(w.e.DB, w.e.Home, v, requestref.Viewer(c), "ids")
		if name, missing := requestref.Missing(err); missing {
			return nil, noItemNamed(name)
		}
		if err != nil {
			return nil, err
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
	ids, err := w.idList(c, p)
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
				if m.id > cur {
					unseen[m.id] = m
					byAgent[m.from] = append(byAgent[m.from], m)
				}
			}
			named := map[string]bool{}
			var inBox []int64
			closed := 0
			for _, i := range ids {
				if m, in := box[i]; in && m.memo {
					if err := closeMemoAsk(tx, i); err != nil {
						return nil, err
					}
					closed++
				} else if in {
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
					if box[i].from == a && box[i].id > upto {
						upto = box[i].id
					}
				}
				owed := int64(0)
				for _, m := range byAgent[a] {
					if (m.gate || m.memo) && (owed == 0 || m.id < owed) {
						owed = m.id
					}
				}
				var top int64
				n := 0
				for _, m := range byAgent[a] {
					if m.id <= upto && !m.gate && !m.memo && (owed == 0 || m.id < owed) {
						n++
						if m.id > top {
							top = m.id
						}
					}
				}
				if n > 0 {
					reads = append(reads, seenMark{a, top})
					read += n
				}
			}
			if read == 0 && closed == 0 {
				for _, i := range inBox {
					if _, in := unseen[i]; in {
						return nil, rpc.Refused("a decision is closed with task.done, never read away", "state", "")
					}
				}
			}
			said = fmt.Sprintf("%d read", read+closed)
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

func inboxText(p map[string]any, key string) (string, error) { return text(p, key) }

func (w *writes) inboxReply(c *rpc.Conn, p map[string]any) (any, error) {
	body, err := text(p, "text")
	if err != nil {
		return nil, err
	}
	rid, err := w.refParam(w.e.DB, c, p, "id", noItemNamed)
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
			var found *mailRow
			for i := range rows {
				if rows[i].id == rid {
					found, target = &rows[i], rows[i].from
				}
			}
			if found == nil || row == nil {
				return nil, nil, noItem(rid)
			}
			if found.gate {
				return nil, nil, rpc.Refused("a decision is a task: close it with task.done, not a reply", "state", "")
			}
		}
		if _, err := w.images.CheckMessage(tx, body, "text", nil, c.Caller.Role == "agent"); err != nil {
			return nil, nil, err
		}
		res, err := SayWrite(w.e, tx, me, target, body, rid, nil)
		if err != nil || !res.OK || c.Caller.Role == "agent" {
			return res, nil, err
		}
		var tid, memo sql.NullInt64
		if err := tx.QueryRow("SELECT memo_id, (SELECT MIN(task_id) FROM task_requests WHERE request_id = id) FROM requests WHERE id = ?", rid).Scan(&memo, &tid); err != nil {
			return nil, nil, err
		}
		if !memo.Valid || !tid.Valid {
			return res, nil, nil
		}
		// An ask a task comment raised is answered on the task: the reply is the founder's comment there, a reply to the comment.
		mid, err := db.AddMemo(tx, tid.Int64, me, body)
		if err != nil {
			return nil, nil, err
		}
		if _, err := tx.Exec("UPDATE task_memos SET reply_to = ? WHERE id = ?", memo.Int64, mid); err != nil {
			return nil, nil, err
		}
		if err := closeMemoAsk(tx, rid); err != nil {
			return nil, nil, err
		}
		tell := res.Tell
		res.Tell = func() string {
			out := tell()
			if w.tell != nil && w.e.Wired() {
				w.tell.MemoChanged(tid.Int64, mid, me, "added", body, true)
			}
			return out
		}
		return res, nil, nil
	})
}
