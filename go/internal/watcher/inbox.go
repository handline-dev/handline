package watcher

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

const (
	humanName    = "human"
	floorEveryS  = 600
	readTouchSQL = "SELECT id, read_at AS touched FROM requests WHERE id >= :floor AND read_at >= :last"
	newestTouch  = "SELECT COALESCE(MAX(m), 0) FROM (SELECT MAX(COALESCE(state_at, ts)) AS m FROM requests " +
		"UNION ALL SELECT MAX(ts) FROM requests WHERE settles IS NOT NULL)"
)

// Inbox is the inbox topic: an item that arrived (inbox.added) or changed, was read or answered (inbox.changed), in the founder's inbox and
// in every agent's. It is change-driven: a pass reads the requests touched since the last one and builds items for those alone; the
// founder's inbox, which is a view over several requests and the for_you_seen cursors, is rebuilt only when one of its requests was touched
// or a cursor moved. What it last said is kept in memory, so the first look after a start says nothing. Every item is the one inbox.list
// returns (reads.FounderItems and reads.AgentItems).
type Inbox struct {
	Reads *reads.Env
	Now   func() time.Time // nil is time.Now

	begun   bool
	since   int64            // when this look began: a request older than that is not new
	floorAt time.Time        // when floor was last worked out
	floor   int64            // the oldest request still looked at
	last    int64            // the newest touch handled
	cursors string           // the for_you_seen cursors as last read
	seen    map[int64]string // agent items as last said, by request
	founder map[int64]string // the founder's items as last said
	items   map[int64]map[string]any
}

func (*Inbox) Name() string { return "inbox" }

func (n *Inbox) now() time.Time {
	if n.Now != nil {
		return n.Now()
	}
	return time.Now()
}

func fingerprint(item map[string]any) string {
	b, _ := json.Marshal(item)
	return string(b)
}

func forYouCursors(q *sql.DB) (string, error) {
	rows, err := q.Query("SELECT name, row_id FROM cursors WHERE name >= 'for_you_seen:' AND name < 'for_you_seen;' ORDER BY name")
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var b strings.Builder
	for rows.Next() {
		var name string
		var id int64
		if err := rows.Scan(&name, &id); err != nil {
			return "", err
		}
		fmt.Fprintf(&b, "%s=%d;", name, id)
	}
	return b.String(), rows.Err()
}

func (n *Inbox) floorNow(q *sql.DB) (int64, error) {
	var floor int64
	err := q.QueryRow("SELECT COALESCE(MIN(id), 0) FROM requests WHERE ts >= ?", n.now().Unix()-recentS).Scan(&floor)
	return floor, err
}

// founderItems is the founder's items from the floor up, by request.
func (n *Inbox) founderItems() (map[int64]map[string]any, error) {
	items, err := n.Reads.FounderItems(n.floor)
	if err != nil {
		return nil, err
	}
	out := map[int64]map[string]any{}
	for _, it := range items {
		out[it["id"].(int64)] = it
	}
	return out, nil
}

type requestRow struct {
	target, requester string
	notice            bool
	ts                int64
}

func requestOf(q *sql.DB, id int64) (*requestRow, error) {
	var r requestRow
	var notice sql.NullInt64
	err := q.QueryRow("SELECT target, requester, notice, ts FROM requests WHERE id = ?", id).Scan(&r.target, &r.requester, &notice, &r.ts)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	r.notice = notice.Valid && notice.Int64 != 0
	return &r, err
}

func (n *Inbox) begin(q *sql.DB) error {
	now := n.now()
	n.since, n.floorAt, n.seen = now.Unix(), now, map[int64]string{}
	var err error
	if n.cursors, err = forYouCursors(q); err != nil {
		return err
	}
	if err = q.QueryRow(newestTouch).Scan(&n.last); err != nil {
		return err
	}
	if n.floor, err = n.floorNow(q); err != nil {
		return err
	}
	if n.items, err = n.founderItems(); err != nil {
		return err
	}
	n.founder = map[int64]string{}
	for id, it := range n.items {
		n.founder[id] = fingerprint(it)
	}
	// the requests touched in the second the mark is at are seen, not news
	rows, err := requestsTouched(q, ChatTouchedSQL, sql.Named("last", n.last))
	if err != nil {
		return err
	}
	for _, r := range rows {
		req, err := requestOf(q, r.id)
		if err != nil {
			return err
		}
		if req != nil && req.target != humanName && !req.notice {
			items, err := n.Reads.AgentItems(req.target, r.id)
			if err != nil {
				return err
			}
			for _, it := range items {
				n.seen[r.id] = fingerprint(it)
			}
		}
	}
	n.begun = true // only a first look that read everything is a first look done: one that failed is taken again
	return nil
}

func (n *Inbox) Scan(q *sql.DB, emit Emit) error {
	if !n.begun {
		return n.begin(q)
	}
	now := n.now()
	if now.Sub(n.floorAt) > floorEveryS*time.Second {
		floor, err := n.floorNow(q)
		if err != nil {
			return err
		}
		n.floorAt, n.floor = now, floor
	}
	chat, err := requestsTouched(q, ChatTouchedSQL, sql.Named("last", n.last))
	if err != nil {
		return err
	}
	read, err := requestsTouched(q, readTouchSQL, sql.Named("floor", n.floor), sql.Named("last", n.last))
	if err != nil {
		return err
	}
	touchedRows := append(chat, read...)
	cursors, err := forYouCursors(q)
	if err != nil {
		return err
	}
	dirty := cursors != n.cursors
	n.cursors = cursors
	ids := map[int64]bool{}
	for _, r := range touchedRows {
		ids[r.id] = true
	}
	sorted := make([]int64, 0, len(ids))
	for id := range ids {
		sorted = append(sorted, id)
	}
	slices.Sort(sorted)
	for _, id := range sorted {
		req, err := requestOf(q, id)
		if err != nil {
			return err
		}
		if req == nil {
			continue
		}
		if req.target == humanName || req.requester == humanName {
			dirty = true
		}
		if req.target == humanName || req.notice {
			continue
		}
		items, err := n.Reads.AgentItems(req.target, id)
		if err != nil {
			return err
		}
		for _, it := range items {
			fp := fingerprint(it)
			if n.seen[id] == fp {
				continue
			}
			_, said := n.seen[id]
			if err := n.say(emit, it, req.target, said || req.ts < n.since); err != nil {
				return err
			}
			n.seen[id] = fp
		}
	}
	if dirty {
		if err := n.rebuildFounder(q, emit); err != nil {
			return err
		}
	}
	for _, r := range touchedRows {
		if r.when.Valid {
			n.last = max(n.last, r.when.Int64)
		}
	}
	return nil
}

// say writes one inbox event for an item; changed says whether it is a change to one that was said or known, not an arrival.
func (n *Inbox) say(emit Emit, item map[string]any, agent string, changed bool) error {
	typ := "inbox.added"
	if changed {
		typ = "inbox.changed"
	}
	title, _ := item["title"].(string)
	_, err := emit(stream.Entry{Topic: "inbox", Type: typ, Data: item, Agent: agent, Title: title,
		Target: map[string]any{"screen": "inbox", "id": fmt.Sprint(item["id"])}})
	return err
}

// rebuildFounder says what differs between the founder's inbox as last said and as it is now: an item new or changed, and an item that left
// it (answered, seen, dismissed or withdrawn), told as changed and read.
func (n *Inbox) rebuildFounder(q *sql.DB, emit Emit) error {
	fresh, err := n.founderItems()
	if err != nil {
		return err
	}
	old, oldItems := n.founder, n.items
	n.items, n.founder = fresh, map[int64]string{}
	for id, it := range fresh {
		n.founder[id] = fingerprint(it)
	}
	ids := make([]int64, 0, len(fresh))
	for id := range fresh {
		ids = append(ids, id)
	}
	slices.Sort(ids)
	for _, id := range ids {
		if old[id] == n.founder[id] {
			continue
		}
		var ts sql.NullInt64
		if err := q.QueryRow("SELECT ts FROM requests WHERE id = ?", id).Scan(&ts); err != nil && err != sql.ErrNoRows {
			return err
		}
		_, had := old[id]
		agent, _ := fresh[id]["agent"].(string)
		if err := n.say(emit, fresh[id], agent, had || (ts.Valid && ts.Int64 < n.since)); err != nil {
			return err
		}
	}
	gone := make([]int64, 0)
	for id := range old {
		if _, here := fresh[id]; !here && id >= n.floor {
			gone = append(gone, id)
		}
	}
	slices.Sort(gone)
	for _, id := range gone {
		answered, err := n.Reads.Answered(id)
		if err != nil {
			return err
		}
		item := map[string]any{}
		for k, v := range oldItems[id] {
			item[k] = v
		}
		item["read"], item["answered"] = true, answered
		agent, _ := item["agent"].(string)
		if err := n.say(emit, item, agent, true); err != nil {
			return err
		}
	}
	return nil
}
