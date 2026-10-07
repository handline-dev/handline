package reads

import (
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/usage"
)

var statsWindows = map[string]int64{"24h": 24 * 3600, "7d": 7 * 24 * 3600}

// workedSeconds is rpc_lists._worked: seconds each agent spent between a prompt and its stop, from the hook events in the window.
func (e *Env) workedSeconds(since, until int64) (map[string]any, error) {
	rows, err := query(e.DB, "SELECT ts, agent, kind FROM events WHERE kind IN ('UserPromptSubmit', 'Stop', 'SessionEnd') AND ts <= ? ORDER BY id", until)
	if err != nil {
		return nil, err
	}
	open := map[string]int64{}
	total := map[string]any{}
	for _, r := range rows {
		agent := r.str("agent")
		if r.str("kind") == "UserPromptSubmit" {
			if _, has := open[agent]; !has {
				open[agent] = r.i("ts")
			}
			continue
		}
		start, has := open[agent]
		if !has {
			continue
		}
		delete(open, agent)
		if span := r.i("ts") - max(start, since); span > 0 {
			prev, _ := total[agent].(int64)
			total[agent] = prev + span
		}
	}
	return total, nil
}

func (e *Env) countEvents(kind string, since, until int64) (int64, error) {
	r, err := queryOne(e.DB, "SELECT COUNT(*) AS n FROM events WHERE kind = ? AND ts >= ? AND ts <= ?", kind, since, until)
	return r.i("n"), err
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
	worked, err := e.workedSeconds(since, until)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"window": window, "since": ts(since), "until": ts(until), "tasks_done": done.i("n"), "working_seconds": worked, "per_account": per}
	for key, kind := range map[string]string{"moves": "moved_account", "handoffs": "task_handoff", "blocks": "block_task"} {
		if out[key], err = e.countEvents(kind, since, until); err != nil {
			return nil, err
		}
	}
	return out, nil
}
