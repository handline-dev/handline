package reads

import (
	"sort"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

// A task's estimate and its expected start and end: deterministic, from the queue ahead of it, its
// owner's account limits and burn, and how long done tasks of its size took. Computed on each read, so it follows them as they change.

// historyN is how many done tasks the duration of a size is read from: the newest of them.
const historyN = 20

// expectation is one read's: the working seconds it has counted, so a task read twice in one pass is read once.
type expectation struct {
	e      *Env
	now    int64
	worked map[int64]int64
}

// of is task.time's expected for task t (a taskRowSQL row) whose time so far is segs: null for an ended task, one the founder holds, or a
// gate.
func (x *expectation) of(t row, segs []stretch) (map[string]any, error) {
	owner := t.str("owner")
	if t.set("ended") || owner == human || owner == db.Gate {
		return nil, nil
	}
	dur, basis, err := x.duration(t)
	if err != nil {
		return nil, err
	}
	current, _, err := db.CurrentTask(x.e.DB, owner)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"start": nil, "end": nil, "duration_s": nil, "basis": nil, "ahead": []int64{}, "wall_until": nil}
	if basis != "" {
		out["duration_s"], out["basis"] = dur, basis
	}
	var start, from int64 // from is where the work still to do begins
	known := true
	if current == t.i("id") {
		start = x.now
		for _, s := range segs {
			if s.rank == segWorking || s.rank == segStarting {
				start = s.from
				break
			}
		}
		from, dur = x.now, dur-workedIn(segs) // what is left of it, from now
	} else {
		start, known, err = x.startAfter(owner, current, t.i("id"), out)
		if err != nil {
			return nil, err
		}
		from = start
	}
	if !known {
		return out, nil
	}
	end := from + max(dur, 0)
	if acct, _ := team.CurrentAccount(x.e.DB, owner); acct != "" {
		blocked, runsOut, resets := x.e.accountWall(acct, x.now)
		if blocked > from {
			end = blocked + max(dur, 0)
			if current != t.i("id") {
				start = blocked
			}
			out["wall_until"] = ts(blocked)
		} else if runsOut != 0 && runsOut < end && resets > runsOut { // the work past the runout waits for the reset
			end += resets - runsOut
			out["wall_until"] = ts(resets)
		}
	}
	out["start"] = ts(start)
	if basis != "" {
		out["end"] = ts(end)
	}
	return out, nil
}

// startAfter is when the task can start: now, after what is left of the owner's current task and the expected time of each task ahead of it
// in the order the daemon hands them out. known is false when one of those has no expected duration.
func (x *expectation) startAfter(owner string, current, tid int64, out map[string]any) (start int64, known bool, err error) {
	start = x.now
	if current != 0 {
		left, ok, err := x.left(current)
		if err != nil || !ok {
			return 0, false, err
		}
		start += left
	}
	waiting, err := db.WaitingTasks(x.e.DB, owner)
	if err != nil {
		return 0, false, err
	}
	me, err := db.GetTask(x.e.DB, tid)
	if err != nil || me == nil {
		return 0, false, err
	}
	var ahead []int64
	for _, w := range waiting {
		if w.Task.ID == tid || w.Task.ID == current || w.Task.Priority > me.Priority || (w.Task.Priority == me.Priority && w.Task.ID > tid) {
			continue
		}
		ahead = append(ahead, w.Task.ID)
	}
	sort.Slice(ahead, func(i, j int) bool { return ahead[i] < ahead[j] })
	out["ahead"] = ahead
	for _, id := range ahead {
		r, err := queryOne(x.e.DB, taskRowSQL, id)
		if err != nil {
			return 0, false, err
		}
		dur, basis, err := x.duration(r)
		if err != nil || basis == "" {
			return 0, false, err
		}
		start += dur
	}
	return start, true, nil
}

// left is the expected rest of task tid, which its owner works on now; ok false when it has no expected duration.
func (x *expectation) left(tid int64) (int64, bool, error) {
	r, err := queryOne(x.e.DB, taskRowSQL, tid)
	if err != nil || r == nil {
		return 0, false, err
	}
	dur, basis, err := x.duration(r)
	if err != nil || basis == "" {
		return 0, false, err
	}
	worked, err := x.workingSeconds(r)
	return max(dur-worked, 0), true, err
}

// duration is how long task t is expected to take, and what that rests on: "history" (done tasks of its estimate), "points" (seconds per
// point over done sized tasks), "time_box" (its time box); "" when nothing does.
func (x *expectation) duration(t row) (int64, string, error) {
	if points := t.i("estimate"); points != 0 {
		same, err := x.history("estimate = ?", points)
		if err != nil {
			return 0, "", err
		}
		if len(same) > 0 {
			return median(same), "history", nil
		}
		perPoint, err := x.history("estimate IS NOT NULL", nil)
		if err != nil {
			return 0, "", err
		}
		if len(perPoint) > 0 {
			return median(perPoint) * points, "points", nil
		}
	}
	if box := t.i("timeout_s"); box > 0 {
		return box, "time_box", nil
	}
	return 0, "", nil
}

// history is the working seconds of the newest done tasks matching where; with arg nil, each divided by its estimate (seconds per point).
func (x *expectation) history(where string, arg any) ([]int64, error) {
	args := []any{historyN}
	if arg != nil {
		args = []any{arg, historyN}
	}
	rows, err := query(x.e.DB, "SELECT id, owner, created_at, estimate, timeout_s, done_at AS ended FROM tasks WHERE done_at IS NOT NULL AND "+
		where+" ORDER BY done_at DESC, id DESC LIMIT ?", args...)
	if err != nil {
		return nil, err
	}
	var out []int64
	for _, r := range rows {
		worked, err := x.workingSeconds(r)
		if err != nil {
			return nil, err
		}
		if arg == nil {
			worked /= r.i("estimate")
		}
		out = append(out, worked)
	}
	return out, nil
}

// workingSeconds is how long task t (a taskRowSQL row) was worked on, by its segments.
func (x *expectation) workingSeconds(t row) (int64, error) {
	if n, ok := x.worked[t.i("id")]; ok {
		return n, nil
	}
	_, _, segs, err := x.e.segments(t, x.now)
	if err != nil {
		return 0, err
	}
	x.worked[t.i("id")] = workedIn(segs)
	return x.worked[t.i("id")], nil
}

func workedIn(segs []stretch) int64 {
	var n int64
	for _, s := range segs {
		if s.rank == segWorking {
			n += s.to - s.from
		}
	}
	return n
}

// median is the middle value, the lower of the two middle ones for an even count: one long outlier does not move it.
func median(xs []int64) int64 {
	s := append([]int64(nil), xs...)
	sort.Slice(s, func(i, j int) bool { return s[i] < s[j] })
	return s[(len(s)-1)/2]
}

// accountWall is AccountWall, from the usage files when it is not set (wallOf, as stats.outlook reads them).
func (e *Env) accountWall(acct string, now int64) (blocked, runsOutAt, resetsAt int64) {
	if e.AccountWall != nil {
		return e.AccountWall(acct, now)
	}
	cfg := usage.LoadConfig(e.Home)
	ue := &usage.Env{Home: e.Home, LoginState: e.LoginState, Config: func() usage.Config { return cfg }}
	in, reset, ok := wallOf(ue, ue.Read(acct), acct, now, nil)
	switch {
	case !ok:
		return 0, 0, 0
	case in == 0:
		return reset, 0, 0
	}
	return 0, now + in, reset
}
