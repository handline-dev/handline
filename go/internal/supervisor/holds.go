package supervisor

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// A task waiting for its owner always says why, and a wait with no cause is told to the founder (daemon/taskholds.py).
//
// The daemon retries every path that can land a task each tick (SweepTaskQueue moves the next task to a free owner, DeliverQueued types it).
// What those paths said when they declined was logged or held in memory, so `hl task list` showed a queued task with nothing beside it. This reads
// the same facts as a level each tick and keeps one line per waiting task in meta, for the readers in other processes.
//
// A reason is either a cause (behind another task, blocked, the owner mid-turn, closed, at its usage limit, a dialog, no terminal) or "the owner
// can take it" - nothing is in the way and the daemon is about to type it. A task that stays in that second state past AbleWarnS has a cause
// nobody named, and the founder is told once.

const (
	// HoldPrefix names the meta rows.
	HoldPrefix = "taskhold:"
	// AbleWarnS: owner able, task still waiting: this long is a missed delivery, not a tick's delay.
	AbleWarnS = 300
	// Ready is the reason of a task nothing is in the way of.
	Ready = "nothing is in the way - the daemon sends it on its next tick"
)

func holdKey(tid int64) string { return fmt.Sprintf("%s%d", HoldPrefix, tid) }

// holdRec is one kept line: why a task waits, whether the owner looks able, since when, and the clocks of the warning.
type holdRec struct {
	Why       string
	Able      bool
	Since     int64
	AbleSince *int64
	Warned    *int64
	order     []string
}

// readHold parses the record a Python or Go sweep kept; nil when there is none or it is not an object.
func readHold(q db.Q, tid int64) *holdRec {
	raw, found := ok2(db.Meta(q, holdKey(tid)))
	if !found || raw == "" {
		return nil
	}
	var m map[string]json.RawMessage
	if json.Unmarshal([]byte(raw), &m) != nil {
		return nil
	}
	rec := &holdRec{}
	for k, v := range m {
		switch k {
		case "why":
			json.Unmarshal(v, &rec.Why)
		case "able":
			json.Unmarshal(v, &rec.Able)
		case "since":
			json.Unmarshal(v, &rec.Since)
		case "able_since":
			var n int64
			if json.Unmarshal(v, &n) == nil {
				rec.AbleSince = &n
			}
		case "warned":
			var n int64
			if json.Unmarshal(v, &n) == nil {
				rec.Warned = &n
			}
		}
	}
	return rec
}

// write is the record as Python's json.dumps writes it, so a Python reader sees the same text.
func (r *holdRec) write(q db.Q, tid int64) {
	yes := map[bool]string{true: "true", false: "false"}
	parts := []string{fmt.Sprintf(`"why": %s`, pyQuote(r.Why)), fmt.Sprintf(`"able": %s`, yes[r.Able]), fmt.Sprintf(`"since": %d`, r.Since)}
	if r.AbleSince != nil {
		parts = append(parts, fmt.Sprintf(`"able_since": %d`, *r.AbleSince))
	}
	if r.Warned != nil {
		parts = append(parts, fmt.Sprintf(`"warned": %d`, *r.Warned))
	}
	check(db.SetMeta(q, holdKey(tid), "{"+strings.Join(parts, ", ")+"}"))
}

// AgentReason is why work is not reaching the agent, and whether it looks able to take it: the daemon's own gates in the order it meets them.
// ("", true) when nothing is in the way.
func (e *Env) agentReason(q db.Q, agent string, now int64) (string, bool) {
	row := stateOf(q, agent)
	if failed := e.FailedReason(agent, now); failed != "" {
		return failed, false
	}
	if blk := must(db.BlockGet(q, agent)); blk != nil {
		return e.Dialogs.Reason(blk.Kind, blk.Detail), false
	}
	if why, _ := e.Stuck.DeliveryBlock(q, agent); why != "" {
		return why, false
	}
	if row != nil && e.Waits.WaitingOnPrompt(q, row) {
		return agent + " is waiting on a permission prompt", false
	}
	if e.clearOwed(q, agent) != 0 {
		return agent + " owes a context clear first (background work is still running)", false
	}
	if why, contestable, ok := e.HeldReason(agent, now); ok {
		// The state says idle and the screen says otherwise: that misread is exactly this.
		return why, contestable && row != nil && (row.State == states.Idle || row.State == states.Queued)
	}
	return "", true
}

// TaskReason is why the task, queued or doing, has not been given to its owner, and whether the owner looks able to take it. ok false when it
// has been (typed, or nothing is waiting to type).
func (e *Env) taskReason(q db.Q, t *db.Task, now int64) (why string, able, ok bool) {
	tid, agent := t.ID, t.Owner
	linked := must(db.TaskRequests(q, tid))
	var req *db.Request
	for _, r := range linked {
		if db.Asks(r) && r.Target == agent {
			req = r
			break
		}
	}
	if agent == db.Human || (!(t.QueuedBy.Valid && t.QueuedBy.String != "") && req == nil) {
		return "", false, false
	}
	if blockers := must(db.TaskBlockers(q, tid)); len(blockers) > 0 {
		parts := make([]string, len(blockers))
		for i, b := range blockers {
			parts[i] = fmt.Sprintf("T%d (%s)", b.ID, b.Owner)
		}
		return "blocked by " + strings.Join(parts, ", "), false, true
	}
	if !e.registered(agent) {
		return agent + " is not registered - nothing will take it", false, true
	}
	holds := must(db.HoldsCurrent(q, t))
	if !holds || (req == nil && must(db.CurrentTaskStillAsking(q, agent))) {
		why := must(db.ChildWait(q, agent))
		if why == "" {
			why = must(db.BlockWait(q, agent))
		}
		if why == "" {
			why = "still open"
		}
		cur, _ := ok2(db.CurrentTask(q, agent))
		return fmt.Sprintf("behind T%d, which %s holds (%s)", cur, agent, why), false, true
	}
	if req == nil {
		var kind, payload string
		err := q.QueryRow("SELECT kind, payload FROM events WHERE CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END = ? ORDER BY id DESC LIMIT 1", tid).Scan(&kind, &payload)
		if err == nil && kind == "task_deliver_refused" {
			var p struct {
				Why any `json:"why"`
			}
			json.Unmarshal([]byte(payload), &p)
			return fmt.Sprintf("its delivery was refused: %s", pyStr(p.Why)), false, true
		}
		return Ready, true, true
	}
	if req.DeliveredAt.Valid && req.DeliveredAt.Int64 != 0 {
		return "", false, false
	}
	if head := e.effectiveHead(q, agent); head != nil && head.ID != req.ID {
		return fmt.Sprintf("behind #%d, which %s has not resolved", head.ID, agent), false, true
	}
	if why, able := e.agentReason(q, agent, now); why != "" {
		return why, able, true
	}
	return Ready, true, true
}

func pyStr(v any) string {
	if v == nil {
		return "None"
	}
	return fmt.Sprint(v)
}

// SweepHolds records why each waiting task waits, drops what no longer waits, and tells the founder once of a task whose owner could take it
// and does not.
func (e *Env) SweepHolds(now int64) (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	seen := map[int64]bool{}
	for _, t := range must(db.OpenTasks(q)) {
		if t.Owner == db.Human || t.DoneEvidence.String != "" {
			continue
		}
		why, able, found := e.taskReason(q, t, now)
		if !found {
			continue
		}
		tid := t.ID
		seen[tid] = true
		rec := readHold(q, tid)
		if rec == nil {
			rec = &holdRec{}
		}
		changed := rec.Why != why || rec.Able != able || readHold(q, tid) == nil
		if changed {
			wasAble := rec.Able
			rec.Why, rec.Able, rec.Since = why, able, now
			if able && !wasAble {
				rec.AbleSince = &now
			}
			if !able {
				rec.AbleSince, rec.Warned = nil, nil
			}
		}
		ableSince := now
		if rec.AbleSince != nil {
			ableSince = *rec.AbleSince
		}
		if able && rec.Warned == nil && now-ableSince >= AbleWarnS {
			after := &After{}
			e.report(q, after, t.Owner, fmt.Sprintf("T%d has waited %d min for %s, which looks able to take it (%s). Delivery should have landed it - this is a missed delivery, not a queue.",
				tid, (now-ableSince)/60, t.Owner, why), "queued task not landing")
			after.Run()
			rec.Warned = &now
			changed = true
			e.log("  T%d: waited %d min for an owner that looks able - founder told", tid, AbleWarnS/60)
		}
		if changed {
			rec.write(q, tid)
		}
	}
	for _, name := range must(db.MetaNames(q, HoldPrefix)) {
		var tid int64
		fmt.Sscan(name[len(HoldPrefix):], &tid)
		if !seen[tid] {
			check(db.ClearMeta(q, name))
		}
	}
	return nil
}
