package usage

import (
	"encoding/json"
	"slices"
	"sort"
	"strconv"

	"github.com/handline/handline/go/internal/db"
)

// The writer half of handline/usage/burnrate.py: observe (what one more agent costs, a sample per stretch of work), the week ratios, and
// the usage_samples log both are read from. The reader half is load.go.

const (
	// SegMinS: a stretch is measured over at least this long, so one point of resolution is a small part of it, and closed at SegMaxS so
	// a run that never changes hands still yields samples.
	SegMinS = 600
	SegMaxS = 1800
	// KeepTotal samples are kept in all, the oldest dropped.
	KeepTotal = 300

	// WeekRatioWindowS is how far back the ratios are read: a plan's ratio is read over days, not hours.
	WeekRatioWindowS = 3 * 86400
	// WeekRatioMinPoints of rise in the longest window a ratio needs, as the week moves in whole points.
	WeekRatioMinPoints = 10
	// WeekRatioEveryS: the daemon recomputes the ratios this often.
	WeekRatioEveryS = 600

	// UsageSampleRetentionDays is db.USAGE_SAMPLE_RETENTION_DAYS.
	UsageSampleRetentionDays = 14
)

func segKey(acct string) string { return "burnseg:" + acct }

// segment is the open stretch of one account: who was working, and the shortest window's first and last live figure.
type segment struct {
	Agents []string `json:"agents"`
	T0     int64    `json:"t0"`
	P0     int      `json:"p0"`
	T1     int64    `json:"t1"`
	P1     int      `json:"p1"`
	Reset  *int64   `json:"reset"`
	Cold   bool     `json:"cold"`
}

func resetOf(w AcctWindow) *int64 {
	if w.Reset == 0 {
		return nil
	}
	r := w.Reset
	return &r
}

func sameReset(a, b *int64) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

func readSegment(q db.Q, acct string) (*segment, error) {
	raw, _, err := db.Meta(q, segKey(acct))
	if err != nil || raw == "" {
		return nil, err
	}
	var s segment
	if json.Unmarshal([]byte(raw), &s) != nil {
		return nil, nil // a stretch that cannot be read is not one
	}
	return &s, nil
}

func loadSampleRows(q db.Q) ([]map[string]any, error) {
	raw, _, err := db.Meta(q, samplesMeta)
	if err != nil {
		return nil, err
	}
	var rows, kept []map[string]any
	if raw != "" && decodeList(raw, &rows) {
		for _, r := range rows {
			if _, ok := num(r["r"]); ok {
				kept = append(kept, r)
			}
		}
	}
	return kept, nil
}

// emit files the stretch as a sample when it was long enough, did not fall, held no cold start and every agent in it is still there.
func (e *Env) emit(q db.Q, acct string, seg *segment) error {
	if seg.T1-seg.T0 < SegMinS || seg.P1 < seg.P0 || seg.Cold || len(seg.Agents) == 0 {
		return nil
	}
	models, efforts := map[string]bool{}, map[string]bool{}
	var model, effort string
	for _, a := range seg.Agents {
		m, f, ok := e.kindOf(a)
		if !ok {
			return nil
		}
		models[m], efforts[f] = true, true
		model, effort = m, f
	}
	sample := map[string]any{"t": seg.T1, "a": acct,
		"r": float64(seg.P1-seg.P0) / float64(seg.T1-seg.T0) / float64(len(seg.Agents)), "m": nil, "e": nil}
	if len(models) == 1 {
		sample["m"] = model
		if len(efforts) == 1 {
			sample["e"] = effort
		}
	}
	rows, err := loadSampleRows(q)
	if err != nil {
		return err
	}
	if len(rows) > KeepTotal-1 {
		rows = rows[len(rows)-(KeepTotal-1):]
	}
	b, err := json.Marshal(append(rows, sample))
	if err != nil {
		return err
	}
	return db.SetMeta(q, samplesMeta, string(b))
}

// Observe is burnrate.observe: one tick's look at an account. active are the agents mid-turn on it, st its reading, and firsts those of
// them in their very first turn since a SessionStart or a move (FirstTurnAgents) - a cold start Measure already prices. It extends the
// open stretch, or closes it when who is working changed or the window rolled over.
func (e *Env) Observe(q db.Q, acct string, active []string, st *AccountStatus, firsts map[string]bool) error {
	seg, err := readSegment(q, acct)
	if err != nil {
		return err
	}
	who := append([]string(nil), active...)
	sort.Strings(who)
	cold := false
	for _, a := range who {
		cold = cold || firsts[a]
	}
	live := st.IsLiveReading()
	if seg != nil && !slices.Equal(seg.Agents, who) {
		if err := e.emit(q, acct, seg); err != nil {
			return err
		}
		seg = nil
	} else if seg != nil && live && (*st.Short.Pct < seg.P1 || !sameReset(resetOf(st.Short), seg.Reset)) {
		// A reading below the last one, or a new reset time, is a rolled window: nothing about the stretch before it is a rate.
		seg = nil
	}
	if len(who) > 0 && live {
		at, pct := *st.Short.At, *st.Short.Pct
		if seg == nil {
			seg = &segment{Agents: who, T0: at, P0: pct, T1: at, P1: pct, Reset: resetOf(st.Short), Cold: cold}
		} else {
			seg.Cold = seg.Cold || cold
			if at > seg.T1 {
				seg.T1, seg.P1 = at, pct
			}
		}
		if seg.T1-seg.T0 >= SegMaxS {
			if err := e.emit(q, acct, seg); err != nil {
				return err
			}
			next := *seg
			next.T0, next.P0, next.Cold = seg.T1, seg.P1, cold
			seg = &next
		}
	}
	if seg == nil {
		return db.SetMeta(q, segKey(acct), "")
	}
	b, err := json.Marshal(seg)
	if err != nil {
		return err
	}
	return db.SetMeta(q, segKey(acct), string(b))
}

// FirstTurnAgents is load.first_turn_agents: agents whose current mid-turn stretch is their very first since the SessionStart or
// moved_account that began it.
func FirstTurnAgents(q db.Q) (map[string]bool, error) {
	rows, err := q.Query("SELECT DISTINCT e1.agent FROM events e1 " +
		"WHERE e1.kind IN ('SessionStart', 'moved_account') " +
		"AND e1.id = (SELECT MAX(e2.id) FROM events e2 WHERE e2.agent = e1.agent " +
		"AND e2.kind IN ('SessionStart', 'moved_account')) " +
		"AND NOT EXISTS (SELECT 1 FROM events e3 WHERE e3.agent = e1.agent " +
		"AND e3.kind = 'Stop' AND e3.id > e1.id)")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]bool{}
	for rows.Next() {
		var a string
		if err := rows.Scan(&a); err != nil {
			return nil, err
		}
		out[a] = true
	}
	return out, rows.Err()
}

// sampleRow is a usage_samples row as windows.entries reads it: every column by name, integers as json.Number.
type sampleRow map[string]any

func readSamples(q db.Q, since int64) ([]sampleRow, error) {
	rows, err := q.Query("SELECT * FROM usage_samples WHERE ts >= ? ORDER BY ts, id", since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	var out []sampleRow
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return nil, err
		}
		r := sampleRow{}
		for i, c := range cols {
			switch v := vals[i].(type) {
			case int64:
				r[c] = json.Number(strconv.FormatInt(v, 10))
			case []byte:
				r[c] = string(v)
			default:
				r[c] = v
			}
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func truthy(v any) bool {
	if b, ok := v.(bool); ok {
		return b
	}
	f, ok := num(v)
	return ok && f != 0
}

func sameFigure(a, b any) bool {
	fa, oka := num(a)
	fb, okb := num(b)
	if oka && okb {
		return fa == fb
	}
	return a == nil && b == nil
}

// LongRatios is burnrate.long_ratios: points of the longest window per point of the shortest, by account and pooled per plan under
// Pooled+plan, from usage_samples rows in time order. Only a pair of consecutive rows of one account that are both live readings of
// both windows, inside the same shortest window and the same longest, and in which neither figure fell, says anything.
func LongRatios(rows []sampleRow, planOf func(acct string) string) map[string]float64 {
	type pair struct{ short, long map[string]any }
	pairOf := func(r sampleRow) *pair {
		es := entries(r)
		if len(es) < 2 {
			return nil
		}
		return &pair{es[0], es[len(es)-1]}
	}
	live := func(p *pair) bool {
		return p.short["used_percent"] != nil && p.long["used_percent"] != nil && p.short["readable"] != false &&
			!(truthy(p.short["carried"]) || truthy(p.short["lost"]) || truthy(p.long["carried"]) || truthy(p.long["lost"]))
	}
	totals := map[string]*[2]float64{}
	last := map[string]*pair{}
	for _, r := range rows {
		a, _ := r["account"].(string)
		cur := pairOf(r)
		prev := last[a]
		last[a] = cur
		if prev == nil || cur == nil || !live(prev) || !live(cur) {
			continue
		}
		if !sameFigure(cur.short["resets_at"], prev.short["resets_at"]) || !sameFigure(cur.long["resets_at"], prev.long["resets_at"]) {
			continue
		}
		cs, _ := num(cur.short["used_percent"])
		ps, _ := num(prev.short["used_percent"])
		cl, _ := num(cur.long["used_percent"])
		pl, _ := num(prev.long["used_percent"])
		ds, dl := cs-ps, cl-pl
		if ds < 0 || dl < 0 {
			continue
		}
		plan := ""
		if planOf != nil {
			plan = planOf(a)
		}
		for _, key := range []string{a, Pooled + plan} {
			t := totals[key]
			if t == nil {
				t = &[2]float64{}
				totals[key] = t
			}
			t[0] += ds
			t[1] += dl
		}
	}
	out := map[string]float64{}
	for k, t := range totals {
		if t[0] > 0 && t[1] >= WeekRatioMinPoints {
			out[k] = t[1] / t[0]
		}
	}
	return out
}

// RefreshLongRatios is burnrate.refresh_long_ratios: recompute the ratios from the last WeekRatioWindowS of samples and keep them, at
// most every WeekRatioEveryS - readers take them from the meta table, so a screen redrawing every couple of seconds never scans samples.
func (e *Env) RefreshLongRatios(q db.Q, now int64, force bool) error {
	raw, _, err := db.Meta(q, weekRatioMeta)
	if err != nil {
		return err
	}
	var kept map[string]any
	if raw != "" && decodeObject(raw, &kept) {
		at, _ := num(kept["at"])
		if !force && float64(now)-at < WeekRatioEveryS {
			return nil
		}
	}
	rows, err := readSamples(q, now-WeekRatioWindowS)
	if err != nil {
		return err
	}
	b, err := json.Marshal(map[string]any{"at": now, "r": LongRatios(rows, e.planKey)})
	if err != nil {
		return err
	}
	return db.SetMeta(q, weekRatioMeta, string(b))
}

// sampleWindow is one window of a usage_samples row, as windows.entry spells it.
type sampleWindow struct {
	Minutes    int    `json:"minutes"`
	UsedPct    *int   `json:"used_percent"`
	ResetsAt   *int64 `json:"resets_at"`
	ObservedAt *int64 `json:"observed_at"`
	Carried    bool   `json:"carried"`
	Lost       bool   `json:"lost"`
	Readable   *bool  `json:"readable,omitempty"`
}

// RecordSample is db.record_usage_sample: one row of what st actually held, at ts (0 is now). Every figure is written as st has it, an
// unknown one as NULL, never 0, so a reader reconstructs the status this was taken from.
func (e *Env) RecordSample(q db.Q, acct string, st *AccountStatus, ts int64) error {
	if ts == 0 {
		ts = int64(e.now())
	}
	ws := []sampleWindow{}
	for _, w := range []AcctWindow{st.Short, st.Long} {
		if w.Minutes == 0 && w.Readable {
			continue
		}
		sw := sampleWindow{Minutes: w.Minutes, UsedPct: w.Pct, ResetsAt: resetOf(w), ObservedAt: w.At, Carried: w.Carried, Lost: w.Lost}
		if !w.Readable {
			f := false
			sw.Readable = &f
		}
		ws = append(ws, sw)
	}
	b, err := json.Marshal(ws)
	if err != nil {
		return err
	}
	var errText any
	if st.Error != "" {
		errText = st.Error
	}
	var collected any
	if st.CollectedAt != nil {
		collected = *st.CollectedAt
	}
	_, err = q.Exec("INSERT INTO usage_samples(account, ts, exists_, error, collected_at, windows) VALUES(?,?,?,?,?,?)",
		acct, ts, st.Exists, errText, collected, string(b))
	return err
}

// PruneSamples is db.prune_usage_samples: delete samples older than the retention window and report how many.
func (e *Env) PruneSamples(q db.Q) (int64, error) {
	return exec(q, "DELETE FROM usage_samples WHERE ts < ?", int64(e.now())-UsageSampleRetentionDays*86400)
}

func exec(q db.Q, query string, args ...any) (int64, error) {
	res, err := q.Exec(query, args...)
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}
