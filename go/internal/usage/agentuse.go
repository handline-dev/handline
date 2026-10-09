package usage

import (
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

// AgentUse is stats.AgentUse: one agent's use over a span, all accounts.
type AgentUse struct {
	Agent  string
	Tokens int64
	Cost   float64 // USD at list price
	Share  float64 // of everyone's cost in the span
	// EstPct is the points of the combined week it used (see AgentUsesOf); nil when no account has a reading.
	EstPct *float64
	// Fracs is its cost share of each account in the span; an account it spent nothing on is absent.
	Fracs map[string]float64
}

type agentTally struct {
	tokens int64 // inp, cc, cr and out: thinking is a part of out
	cost   float64
}

// AgentUsesOf is stats.agent_use: each agent's tokens and list-price cost in (lo, hi], largest cost first, ties by name. weekUsed is the
// points of each account's week used in the span (WeekConsumed): an agent's EstPct is its cost share of each account times that account's
// points, averaged over the accounts, an estimate because what the plan charges is not what the price list says.
func AgentUsesOf(q db.Q, lo, hi int64, table []priceEntry, weekUsed map[string]float64) ([]AgentUse, error) {
	trees, err := worktrees(q)
	if err != nil {
		return nil, err
	}
	rows, err := q.Query("SELECT account, cwd, model, inp, cc, cr, out, thinking FROM token_calls WHERE ts > ? AND ts <= ? ORDER BY ts, rowid", lo, hi)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	type key struct{ agent, acct string }
	per := map[key]*agentTally{}
	for rows.Next() {
		var acct, cwd, model string
		var t Tokens
		if err := rows.Scan(&acct, &cwd, &model, &t.Inp, &t.CC, &t.CR, &t.Out, &t.Thinking); err != nil {
			return nil, err
		}
		k := key{agentForCwd(cwd, trees), acct}
		if per[k] == nil {
			per[k] = &agentTally{}
		}
		per[k].tokens += t.Inp + t.CC + t.CR + t.Out
		per[k].cost += Cost(model, t, table)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	keys := make([]key, 0, len(per))
	for k := range per {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { // a fixed order keeps the float sums the same on every run
		return keys[i].agent < keys[j].agent || (keys[i].agent == keys[j].agent && keys[i].acct < keys[j].acct)
	})
	acctCost := map[string]float64{}
	for _, k := range keys {
		acctCost[k.acct] += per[k].cost
	}
	est := map[string]float64{}
	by := map[string]*AgentUse{}
	var total float64
	for _, k := range keys {
		t := per[k]
		u := by[k.agent]
		if u == nil {
			u = &AgentUse{Agent: k.agent, Fracs: map[string]float64{}}
			by[k.agent] = u
		}
		u.Tokens += t.tokens
		u.Cost += t.cost
		total += t.cost
		if acctCost[k.acct] == 0 {
			continue
		}
		u.Fracs[k.acct] = t.cost / acctCost[k.acct]
		if pts, ok := weekUsed[k.acct]; ok {
			est[k.agent] += t.cost / acctCost[k.acct] * pts / float64(len(weekUsed))
		}
	}
	out := make([]AgentUse, 0, len(by))
	for _, u := range by {
		if total != 0 {
			u.Share = u.Cost / total
		}
		if len(weekUsed) > 0 {
			e := est[u.Agent]
			u.EstPct = &e
		}
		out = append(out, *u)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Cost > out[j].Cost || (out[i].Cost == out[j].Cost && out[i].Agent < out[j].Agent)
	})
	return out, nil
}

// PickUse is the use of a pick of the agents table: an agent, or "grp:<name>" for the agent <name> and every <name>.* together (fractions
// of the account added up, as group_uses does). ok is false for a name that is neither.
func PickUse(uses []AgentUse, pick string) (AgentUse, bool) {
	group, isGroup := strings.CutPrefix(pick, "grp:")
	sum := AgentUse{Agent: pick, Fracs: map[string]float64{}}
	found := false
	for _, u := range uses {
		if (isGroup && (u.Agent == group || strings.HasPrefix(u.Agent, group+"."))) || (!isGroup && u.Agent == pick) {
			found = true
			for a, f := range u.Fracs {
				sum.Fracs[a] += f
			}
		}
	}
	return sum, found
}

// WeekConsumed is outlook.week_consumed: the points of each account's week used between lo and hi, the rises from one sample to the next
// and after a reset what has been used since. The last sample before lo (not older than OutlookMaxAgeS) is where the first rise starts from.
func WeekConsumed(rows []sampleRow, lo, hi int64) map[string]float64 {
	by := map[string][]weekPoint{}
	inside := map[string]bool{}
	for _, r := range rows {
		w, ok := weekOf(r)
		if !ok || w.TS > hi {
			continue
		}
		a, _ := r["account"].(string)
		if w.TS >= lo {
			by[a] = append(by[a], w)
			inside[a] = true
		} else if lo-w.TS <= OutlookMaxAgeS {
			by[a] = []weekPoint{w} // the closest one before lo (rows are oldest first)
		}
	}
	out := map[string]float64{}
	for a, series := range by {
		if !inside[a] {
			continue // nothing read inside the span: not an account it says anything of
		}
		var total float64
		for i := 1; i < len(series); i++ {
			total += rise(series[i-1], series[i])
		}
		out[a] = total
	}
	return out
}

func rise(prev, cur weekPoint) float64 {
	if cur.Used >= prev.Used {
		return cur.Used - prev.Used
	}
	return cur.Used
}

// ConsumedCurve is outlook.consumed_curve: for each account, the points of its week used from the first time to each of times.
func ConsumedCurve(rows []sampleRow, times []int64) map[string][]float64 {
	by := map[string][]weekPoint{}
	for _, r := range rows {
		if w, ok := weekOf(r); ok {
			a, _ := r["account"].(string)
			by[a] = append(by[a], w)
		}
	}
	out := map[string][]float64{}
	for a, series := range by {
		cum := make([]float64, 0, len(times))
		var run float64
		var prev *weekPoint
		i := 0
		for _, t := range times {
			for i < len(series) && series[i].TS <= t {
				cur := series[i]
				if prev != nil && cur.TS >= times[0] {
					run += rise(*prev, cur)
				}
				prev = &series[i]
				i++
			}
			cum = append(cum, run)
		}
		out[a] = cum
	}
	return out
}

// Samples are the usage_samples rows WeeklyOutlook and the use of each agent read.
type Samples = []sampleRow

// Overlay is agent_overlay: the points of the combined week the pick had used by each time of past, its cost share of each account times
// what that account had used so far, averaged over the accounts. Empty when the pick used nothing or is no agent or group.
func Overlay(rows Samples, uses []AgentUse, pick string, past []OutlookPoint) []float64 {
	use, found := PickUse(uses, pick)
	if !found || len(use.Fracs) == 0 {
		return nil
	}
	times := make([]int64, len(past))
	for i, p := range past {
		times[i] = p.At
	}
	curves := ConsumedCurve(rows, times)
	accts := make([]string, 0, len(curves))
	for a := range curves {
		accts = append(accts, a)
	}
	sort.Strings(accts)
	n := float64(max(len(curves), 1))
	out := make([]float64, len(times))
	for k := range times {
		var sum float64
		for _, a := range accts {
			sum += use.Fracs[a] * curves[a][k]
		}
		out[k] = sum / n
	}
	return out
}
