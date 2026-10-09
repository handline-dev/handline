package reads

import (
	"sort"
	"strconv"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

// outlookHours is the span of each range: how far back and ahead the headroom curve goes, and how many days the cost covers.
var outlookHours = map[string]int{"12h": 12, "24h": 24, "3d": 72, "7d": 168, "14d": 336, "30d": 720}

var outlookRanges = "12h, 24h, 3d, 7d, 14d, 30d"

// round is Python's round(v, places): the nearest decimal to the exact binary value.
func round(v float64, places int) float64 {
	f, _ := strconv.ParseFloat(strconv.FormatFloat(v, 'f', places, 64), 64)
	return f
}

func points(series []usage.OutlookPoint) []any {
	out := make([]any, 0, len(series))
	for _, p := range series {
		var left any
		if p.Left != nil {
			left = round(*p.Left, 2)
		}
		out = append(out, map[string]any{"at": ts(p.At), "left_pct": left})
	}
	return out
}

// runsOut is rpc_outlook._runs_out: seconds until the window that runs out first does, as the runway's words pick it; nil when there is
// no reading, no burn to go by, or the window resets first.
func runsOut(e *usage.Env, st *usage.AccountStatus, acct string, now int64, ld *usage.Load) any {
	var burn *float64
	if b, ok := e.ReadBurn(acct); ok {
		burn = &b
	}
	r := usage.RunwayOf(st, burn, float64(now), ld)
	if r == nil {
		return nil
	}
	if r.LeftShort == 0 || (r.LeftLong != nil && *r.LeftLong == 0) {
		return 0
	}
	var five *float64
	if r.Burning && !r.PastReset {
		five = r.EtaS
	}
	if r.LongS != nil && (five == nil || *r.LongS < *five) {
		return int64(*r.LongS)
	}
	if five == nil {
		return nil
	}
	return int64(*five)
}

// statsOutlook is stats.outlook: the week left of the accounts together (recorded, then guessed), each account's runway and what the
// agents cost per day.
func (e *Env) statsOutlook(_ *rpc.Conn, params map[string]any) (any, error) {
	name, _ := params["range"].(string)
	hours, ok := outlookHours[name]
	if !ok {
		return nil, badParam("range is one of "+outlookRanges, "range")
	}
	pick, err := optStr(params, "agent")
	if err != nil {
		return nil, err
	}
	return e.outlookAt(name, hours, time.Now(), pick)
}

// agentUseOf is the spec's agent_use row.
func agentUseOf(u usage.AgentUse) map[string]any {
	var est any
	if u.EstPct != nil {
		est = round(*u.EstPct, 2)
	}
	fracs := map[string]any{}
	for a, f := range u.Fracs {
		fracs[a] = round(f, 4)
	}
	return map[string]any{"agent": u.Agent, "tokens": u.Tokens, "cost": round(u.Cost, 4), "share": round(u.Share, 4), "est_pct": est, "fracs": fracs}
}

// overlayOf is the spec's overlay, at two places like left_pct.
func overlayOf(rows usage.Samples, uses []usage.AgentUse, pick string, past []usage.OutlookPoint) []any {
	out := []any{}
	for _, v := range usage.Overlay(rows, uses, pick, past) {
		out = append(out, round(v, 2))
	}
	return out
}

func (e *Env) outlookAt(name string, hours int, clock time.Time, pick *string) (any, error) {
	now := clock.Unix()
	days := max(1, hours/24)
	ue := &usage.Env{Home: e.Home, Now: func() float64 { return float64(now) }}
	cfg := ue.CurrentConfig()
	rows, err := usage.SamplesSince(e.DB, now-int64(hours)*3600-usage.OutlookMaxAgeS)
	if err != nil {
		return nil, err
	}
	// An account that ends is cut at its end; one logged out is cut from when it was found so, and counted again from the login.
	ends := map[string]float64{}
	for _, a := range usage.SampleAccounts(rows) {
		cut, has := 0.0, false
		if t, ok := cfg.EndOf(e.Home, a, clock); ok {
			cut, has = float64(t.UnixNano())/1e9, true
		}
		if since, gone := (team.Accounts{Home: e.Home}).GoneSince(a, float64(now)); gone && (!has || since < cut) {
			cut, has = since, true
		}
		if has {
			ends[a] = cut
		}
	}
	out := usage.WeeklyOutlook(rows, now, hours, ends)
	first := time.Date(clock.Year(), clock.Month(), clock.Day()-(days-1), 0, 0, 0, 0, time.Local)
	cost, err := usage.DailyCost(e.DB, first.Unix()-1, now, usage.PricesFromConfig(cfg))
	if err != nil {
		return nil, err
	}
	loads, err := ue.ReadAll(e.DB, "")
	if err != nil {
		return nil, err
	}
	runway := []any{}
	for _, a := range paths.ListAccounts(e.Home) {
		st := ue.Read(a)
		var runs any
		if !st.Unmetered {
			runs = runsOut(ue, st, a, now, loads[a])
		}
		var resets any
		if st.Long.Reset != 0 {
			resets = ts(st.Long.Reset)
		}
		runway = append(runway, map[string]any{"account": a, "runs_out_in_seconds": runs, "week_resets_at": resets})
	}
	perDay := []any{}
	days_ := make([]string, 0, len(cost))
	for d := range cost {
		days_ = append(days_, d)
	}
	sort.Strings(days_)
	for _, d := range days_ {
		agents := map[string]any{}
		for a, c := range cost[d] {
			agents[a] = round(c, 4)
		}
		perDay = append(perDay, map[string]any{"day": d, "agents": agents})
	}
	uses, err := usage.AgentUsesOf(e.DB, now-int64(hours)*3600, now, usage.PricesFromConfig(cfg), usage.WeekConsumed(rows, now-int64(hours)*3600, now))
	if err != nil {
		return nil, err
	}
	agentUse := []any{}
	for _, u := range uses {
		agentUse = append(agentUse, agentUseOf(u))
	}
	res := map[string]any{"range": name, "now": ts(now), "accounts": out.Accounts, "past": points(out.Past), "future": points(out.Future),
		"runway": runway, "cost_per_day": perDay, "agent_use": agentUse}
	if pick != nil {
		res["overlay"] = overlayOf(rows, uses, *pick, out.Past)
	}
	return res, nil
}
