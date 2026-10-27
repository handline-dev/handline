package reads

import (
	"fmt"
	"sort"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// The waiting reasons a segment names, in the words of docs/story1-research.md ("One picture of what waits and why"). They live here alone so
// the shared state and reason list can replace them in one place.
const (
	waitFounder = "waiting on you"
	waitTask    = "blocked by T%d"
	waitAgent   = "waiting on %s"
	waitWall    = "wall"
	waitQueued  = "queued"
	startText   = "agent starting"
)

// A segment's kind, and its rank where two overlap: the higher one is what the stretch shows.
const (
	segWorking = iota
	segStarting
	segAccount
	segQueued
	segAgent
	segTask
	segFounder
)

var segKinds = map[int]struct{ kind, on string }{
	segWorking: {"working", ""}, segStarting: {"starting", ""}, segAccount: {"waiting", "account"}, segAgent: {"waiting", "agent"},
	segTask: {"waiting", "task"}, segFounder: {"waiting", "founder"}, segQueued: {"queued", ""},
}

type stretch struct {
	from, to int64
	rank     int
	text     string
	task     int64
}

// taskTime is task.time: where a task's time went, from its opening to its end (or now), as segments that cover it with no gap or overlap.
func (e *Env) taskTime(c *rpc.Conn, params map[string]any) (any, error) {
	tid, err := optInt(params, "task")
	if err != nil || tid == nil {
		return nil, badParam("task is a task number", "task")
	}
	return e.taskTimeAt(*tid, time.Now().Unix())
}

// taskRowSQL is the task row task.time and the expectation read.
const taskRowSQL = "SELECT id, owner, created_at, estimate, timeout_s, COALESCE(done_at, discarded_at, closed_at) AS ended FROM tasks WHERE id = ?"

func (e *Env) taskTimeAt(tid, now int64) (any, error) {
	t, err := queryOne(e.DB, taskRowSQL, tid)
	if err != nil {
		return nil, err
	}
	if t == nil {
		nf := rpc.NotFoundError(fmt.Sprintf("no task T%d", tid))
		nf.Data = map[string]any{"what": "task", "id": tid}
		return nil, nf
	}
	start, end, segs, err := e.segments(t, now)
	if err != nil {
		return nil, err
	}
	out := make([]map[string]any, len(segs))
	for i, s := range segs {
		k := segKinds[s.rank]
		seg := map[string]any{"kind": k.kind, "from": ts(s.from), "to": ts(s.to), "seconds": s.to - s.from, "text": s.text, "on": nil, "task": nil}
		if k.on != "" {
			seg["on"] = k.on
		}
		if s.task != 0 {
			seg["task"] = s.task
		}
		out[i] = seg
	}
	var endAt any
	if t.set("ended") {
		endAt = ts(end)
	}
	expected, err := (&expectation{e: e, now: now, worked: map[int64]int64{}}).of(t, segs)
	if err != nil {
		return nil, err
	}
	return map[string]any{"task": tid, "start": ts(start), "end": endAt, "segments": out, "estimate": t.intOrNil("estimate"), "expected": expected}, nil
}

// segments is the task's time from its opening to its end (or now), covered with no gap or overlap.
func (e *Env) segments(t row, now int64) (start, end int64, segs []stretch, err error) {
	start, end = t.i("created_at"), now
	if t.set("ended") {
		end = t.i("ended")
	}
	spans, err := e.taskSpans(t, start, end)
	if err != nil {
		return 0, 0, nil, err
	}
	return start, end, cover(spans, start, end), nil
}

// taskSpans is every stretch the db records for the task, overlapping as they happened: its state (queued, doing, reported), its blockers,
// its asks of the founder, and its holder's parks and starts while it was doing.
func (e *Env) taskSpans(t row, start, end int64) ([]stretch, error) {
	tid := t.i("id")
	events, err := query(e.DB, "SELECT ts, agent, kind, payload FROM events WHERE "+eventTask+" = ? AND kind IN "+
		"('queue_task', 'draft_task', 'task_begin', 'task_start', 'task_report', 'accept_task', 'reject_task', 'reopen_task', 'release_task', "+
		"'task_handoff', 'edit_task', 'block_task', 'unblock_task') ORDER BY id", tid)
	if err != nil {
		return nil, err
	}
	// A delivery that landed in its holder's session counts as the work begun, like a task_start; it is read as one more event in time order.
	landed, err := query(e.DB, "SELECT r.landed_at AS ts, r.target FROM requests r JOIN task_requests l ON l.request_id = r.id "+
		"WHERE l.task_id = ? AND r.landed_at IS NOT NULL AND r.target != 'human' ORDER BY r.landed_at, r.id", tid)
	if err != nil {
		return nil, err
	}
	for _, d := range landed {
		d["kind"] = "delivered"
		i := sort.Search(len(events), func(i int) bool { return events[i].i("ts") > d.i("ts") })
		events = append(events, nil)
		copy(events[i+1:], events[i:])
		events[i] = d
	}
	var spans []stretch
	open := func(from int64, rank int, text string, task int64) int {
		spans = append(spans, stretch{from: from, to: end, rank: rank, text: text, task: task})
		return len(spans) - 1
	}
	// The state: queued until the holder has it, doing until it is reported, waiting on whoever accepts a report.
	state := open(start, segQueued, waitQueued, 0)
	var doing [][2]int64
	holder := t.str("owner")
	current := holder
	holders := map[int64]string{}
	blocks := map[int64]int{}
	for _, ev := range events {
		at, p := ev.i("ts"), decodeObject(ev.str("payload"))
		cut := func() {
			if spans[state].rank == segWorking {
				doing = append(doing, [2]int64{spans[state].from, at})
			}
			spans[state].to = at
		}
		switch ev.str("kind") {
		case "queue_task", "draft_task", "reopen_task", "reject_task", "release_task":
			cut()
			state = open(at, segQueued, waitQueued, 0)
		case "delivered":
			if spans[state].rank == segQueued && ev.str("target") == current {
				cut()
				state = open(at, segWorking, "", 0)
			}
		case "task_begin", "task_start":
			if spans[state].rank != segWorking {
				cut()
				state = open(at, segWorking, "", 0)
			}
		case "task_report":
			cut()
			if by := text(p["to"]); by != "" && by != "human" {
				state = open(at, segAgent, fmt.Sprintf(waitAgent, by), 0)
			} else {
				state = open(at, segFounder, waitFounder, 0)
			}
		case "accept_task":
			cut()
		case "task_handoff":
			if to := text(p["to"]); to != "" {
				holders[at], current = to, to
			}
		case "edit_task":
			if o, ok := p["owner"].(map[string]any); ok {
				holders[at], current = text(o["new"]), text(o["new"])
			}
		case "block_task":
			on, _ := recInt(p["blocks_on"])
			if i, ok := blocks[on]; ok {
				spans[i].to = at // blocked again on the same task: the new span carries on from here
			}
			blocks[on] = open(at, segTask, fmt.Sprintf(waitTask, on), on)
		case "unblock_task":
			on, _ := recInt(p["blocks_on"])
			if i, ok := blocks[on]; ok {
				spans[i].to = at
				delete(blocks, on)
			}
		}
	}
	if spans[state].rank == segWorking {
		doing = append(doing, [2]int64{spans[state].from, spans[state].to})
	}
	// A blocker ends when it is done or discarded, if no unblock said so first.
	for on, i := range blocks {
		b, err := queryOne(e.DB, "SELECT COALESCE(done_at, discarded_at) AS ended FROM tasks WHERE id = ?", on)
		if err != nil {
			return nil, err
		}
		if b.set("ended") && b.i("ended") < spans[i].to {
			spans[i].to = b.i("ended")
		}
	}
	asks, err := query(e.DB, "SELECT r.ts, r.state, r.state_at FROM requests r JOIN task_requests l ON l.request_id = r.id "+
		"WHERE l.task_id = ? AND r.target = 'human' AND COALESCE(r.kind, '') != 'say'", tid)
	if err != nil {
		return nil, err
	}
	for _, a := range asks {
		i := open(a.i("ts"), segFounder, waitFounder, 0)
		switch {
		case a.str("state") == "queued":
		case a.set("state_at"):
			spans[i].to = a.i("state_at")
		default: // closed with no time on record: it ended by the task's next event at the latest
			for _, ev := range events {
				if ev.i("ts") > a.i("ts") {
					spans[i].to = ev.i("ts")
					break
				}
			}
		}
	}
	// The holder's parks and starts count only while it held the task and was doing it.
	for _, d := range doing {
		who := holderAt(holder, holders, d[0])
		more, err := e.holderSpans(who, d[0], d[1])
		if err != nil {
			return nil, err
		}
		spans = append(spans, more...)
	}
	return spans, nil
}

// holderAt is who held the task at moment at: the last hand-off or reassignment before it, else the holder it was opened with.
func holderAt(first string, changes map[int64]string, at int64) string {
	var best int64 = -1
	who := first
	for when, to := range changes {
		if when <= at && when > best {
			best, who = when, to
		}
	}
	return who
}

// holderSpans is agent's parks (at an account's wall until it woke) and starts (a launch, a wake or a session hand-off until the session
// was up) within [from, to].
func (e *Env) holderSpans(agent string, from, to int64) ([]stretch, error) {
	evs, err := query(e.DB, "SELECT ts, kind FROM events WHERE agent = ? AND kind IN ('park', 'wake', 'launching', 'handoff') AND ts <= ? ORDER BY id", agent, to)
	if err != nil {
		return nil, err
	}
	starts, err := query(e.DB, "SELECT ts FROM session_starts WHERE agent = ? AND ts >= ? ORDER BY ts", agent, from)
	if err != nil {
		return nil, err
	}
	upAfter := func(at int64) int64 {
		for _, s := range starts {
			if s.i("ts") >= at {
				return s.i("ts")
			}
		}
		return to
	}
	var out []stretch
	parked := -1
	for _, ev := range evs {
		at := ev.i("ts")
		if ev.str("kind") == "park" {
			out = append(out, stretch{from: at, to: to, rank: segAccount, text: waitWall})
			parked = len(out) - 1
			continue
		}
		if parked >= 0 {
			out[parked].to = at
			parked = -1
		}
		if at >= from {
			out = append(out, stretch{from: at, to: upAfter(at), rank: segStarting, text: startText})
		}
	}
	for i := range out {
		out[i].from, out[i].to = max(out[i].from, from), min(out[i].to, to)
	}
	return out, nil
}

// cover lays spans on [start, end]: each stretch shows the highest-ranked span over it, and touching stretches that read the same are one.
func cover(spans []stretch, start, end int64) []stretch {
	cuts := []int64{start, end}
	for _, s := range spans {
		if s.from < s.to {
			cuts = append(cuts, max(start, min(end, s.from)), max(start, min(end, s.to)))
		}
	}
	sort.Slice(cuts, func(i, j int) bool { return cuts[i] < cuts[j] })
	var out []stretch
	for i := 0; i+1 < len(cuts); i++ {
		a, b := cuts[i], cuts[i+1]
		if a == b {
			continue
		}
		top := stretch{rank: -1}
		for _, s := range spans {
			if s.from <= a && s.to >= b && s.from < s.to && s.rank > top.rank {
				top = s
			}
		}
		if top.rank < 0 {
			continue
		}
		if n := len(out); n > 0 && out[n-1].to == a && out[n-1].rank == top.rank && out[n-1].text == top.text {
			out[n-1].to = b
			continue
		}
		out = append(out, stretch{from: a, to: b, rank: top.rank, text: top.text, task: top.task})
	}
	return out
}
