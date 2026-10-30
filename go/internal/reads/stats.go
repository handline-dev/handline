package reads

import (
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/usage"
)

var statsWindows = map[string]int64{"24h": 24 * 3600, "7d": 7 * 24 * 3600}

func (e *Env) countEvents(kind string, since, until int64) (int64, error) {
	r, err := queryOne(e.DB, "SELECT COUNT(*) AS n FROM events WHERE kind = ? AND ts >= ? AND ts <= ?", kind, since, until)
	return r.i("n"), err
}

// statsUse is stats.get's use: a row per agent and account, the account's weekly rise split by list-price cost.
func (e *Env) statsUse(since, until int64) ([]any, error) {
	ue := &usage.Env{Home: e.Home, Now: func() float64 { return float64(until) }}
	rows, err := usage.SamplesSince(e.DB, since-usage.OutlookMaxAgeS)
	if err != nil {
		return nil, err
	}
	uses, err := usage.PairUsesOf(e.DB, since, until, usage.PricesFromConfig(ue.CurrentConfig()), usage.WeekConsumed(rows, since, until))
	if err != nil {
		return nil, err
	}
	out := []any{}
	for _, u := range uses {
		var week any
		if u.WeekPct != nil {
			week = round(*u.WeekPct, 2)
		}
		out = append(out, map[string]any{"agent": u.Agent, "account": u.Account, "tokens": u.Tokens, "cost": round(u.Cost, 4), "share": round(u.Share, 4), "week_pct": week})
	}
	return out, nil
}

// statsGet is stats.get: what the Stats screen draws for the last 24 hours or 7 days.
func (e *Env) statsGet(_ *rpc.Conn, params map[string]any) (any, error) {
	window, _ := params["window"].(string)
	span, ok := statsWindows[window]
	if !ok {
		return nil, badParam("window is 24h or 7d", "window")
	}
	until := time.Now().Unix()
	since := until - span
	done, err := queryOne(e.DB, "SELECT COUNT(*) AS n FROM tasks WHERE done_at >= ? AND done_at <= ?", since, until)
	if err != nil {
		return nil, err
	}
	per := []any{}
	for _, a := range paths.ListAccounts(e.Home) {
		st := usage.ReadStatus(e.Home, a)
		pct := func(w usage.Window) any {
			if w.Pct == nil {
				return nil
			}
			return float64(*w.Pct)
		}
		per = append(per, map[string]any{"account": a, "five_hour_used_pct": pct(st.Short), "weekly_used_pct": pct(st.Long)})
	}
	use, err := e.statsUse(since, until)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"window": window, "since": ts(since), "until": ts(until), "tasks_done": done.i("n"), "use": use, "per_account": per}
	for key, kind := range map[string]string{"moves": "moved_account", "handoffs": "task_handoff", "blocks": "block_task"} {
		if out[key], err = e.countEvents(kind, since, until); err != nil {
			return nil, err
		}
	}
	return out, nil
}
