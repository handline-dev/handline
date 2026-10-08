package usage

import (
	"database/sql"
	"math"
	"sort"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

// Burn-rate costs and per-account load (handline/usage/burnrate.py's reader half, load.py). The writers (observe, the week ratios) are
// in burn.go: with no samples a typical agent's starting value stands.

const (
	// TypicalAgentBurnPerS is triggers.TYPICAL_AGENT_BURN_PER_S: the starting value for what one agent costs, percent of the shortest
	// window per second.
	TypicalAgentBurnPerS = 0.75 / 60
	// MinStretchS is the least stretch worth beginning: below it a start costs more than it gets back.
	MinStretchS = 30 * 60

	Keep       = 15
	MinSamples = 5
	// FloorPerS and CeilingPerS keep an estimate inside what an agent mid-turn can cost.
	FloorPerS   = 0.1 / 60
	CeilingPerS = 2.0 / 60

	// WakeReserveS: a reservation no launch answered in this long is let go.
	WakeReserveS = 300

	samplesMeta   = "burnsamples"
	weekRatioMeta = "weekratio"
	// Pooled prefixes a plan's pooled week ratio.
	Pooled = "*"
)

// StretchS is triggers.stretch_s: how long an account lasts before its wall with one more agent on it, at that agent's cost.
func StretchS(headroomPct, fillPerS, agentPerS float64) float64 {
	rate := max(0.0, fillPerS) + agentPerS
	if rate > 0 {
		return headroomPct / rate
	}
	return math.Inf(1)
}

// Sample is one burn sample: when, which account, the rate, and the model and effort when every agent in it shared them.
type Sample struct {
	R    float64
	M, E string // "" is none
}

// Costs is what one more agent costs, read once from the samples and shared by every account's load.
type Costs struct {
	samples    []Sample // oldest first
	longRatios map[string]float64
	planOf     func(acct string) string
	kindOf     func(agent string) (model, effort string, ok bool)
	plans      map[string]string
}

func (c *Costs) plan(acct string) string {
	if p, ok := c.plans[acct]; ok {
		return p
	}
	if c.plans == nil {
		c.plans = map[string]string{}
	}
	p := ""
	if c.planOf != nil {
		p = c.planOf(acct)
	}
	c.plans[acct] = p
	return p
}

// LongPerShort is the points of the longest window one point of the shortest costs on this account: its own measured ratio, else its
// plan's pooled one, else ok false - nothing measured it, and no shortest-window figure can be turned into the longest's.
func (c *Costs) LongPerShort(acct string) (float64, bool) {
	if r, ok := c.longRatios[acct]; ok {
		return r, true
	}
	r, ok := c.longRatios[Pooled+c.plan(acct)]
	return r, ok
}

func median(v []float64) float64 {
	s := append([]float64(nil), v...)
	sort.Float64s(s)
	n := len(s)
	if n%2 == 1 {
		return s[n/2]
	}
	return (s[n/2-1] + s[n/2]) / 2
}

func (c *Costs) median(pick func(Sample) bool) (float64, int, bool) {
	var vals []float64
	for i := len(c.samples) - 1; i >= 0 && len(vals) < Keep; i-- {
		if pick(c.samples[i]) {
			vals = append(vals, c.samples[i].R)
		}
	}
	if len(vals) < MinSamples {
		return 0, 0, false
	}
	return median(vals), len(vals), true
}

// Describe is (percent per second, where it came from, samples behind it): the agent's kind, else its model's pool, else all agents',
// else the starting value.
func (c *Costs) Describe(agent string) (float64, string, int) {
	type tier struct {
		name string
		pick func(Sample) bool
	}
	var tiers []tier
	if agent != "" && c.kindOf != nil {
		if model, effort, ok := c.kindOf(agent); ok {
			tiers = append(tiers, tier{model + " " + effort, func(s Sample) bool { return s.M == model && s.E == effort }},
				tier{model, func(s Sample) bool { return s.M == model }})
		}
	}
	tiers = append(tiers, tier{"all agents", func(Sample) bool { return true }})
	for _, t := range tiers {
		if m, n, ok := c.median(t.pick); ok {
			return min(CeilingPerS, max(FloorPerS, m)), t.name, n
		}
	}
	return TypicalAgentBurnPerS, "starting value", 0
}

// PerS is what one agent of agent's kind costs, percent per second.
func (c *Costs) PerS(agent string) float64 { p, _, _ := c.Describe(agent); return p }

// LoadCosts reads the samples and the week ratios from the meta table.
func (e *Env) LoadCosts(q db.Q) (*Costs, error) {
	c := &Costs{planOf: e.planKey, kindOf: e.kindOf, longRatios: map[string]float64{}}
	raw, _, err := db.Meta(q, samplesMeta)
	if err != nil {
		return nil, err
	}
	var rows []map[string]any
	if raw != "" && decodeList(raw, &rows) {
		for _, r := range rows {
			f, ok := num(r["r"])
			if !ok {
				continue
			}
			s := Sample{R: f}
			s.M, _ = r["m"].(string)
			s.E, _ = r["e"].(string)
			c.samples = append(c.samples, s)
		}
	}
	raw, _, err = db.Meta(q, weekRatioMeta)
	if err != nil {
		return nil, err
	}
	var kept map[string]any
	if raw != "" && decodeObject(raw, &kept) {
		if rs, ok := kept["r"].(map[string]any); ok {
			for k, v := range rs {
				if f, isNum := num(v); isNum {
					c.longRatios[k] = f
				}
			}
		}
	}
	return c, nil
}

func decodeList(raw string, out *[]map[string]any) bool {
	var v any
	if decodeNumbers([]byte(raw), &v) != nil {
		return false
	}
	list, ok := v.([]any)
	if !ok {
		return false
	}
	for _, x := range list {
		if m, isMap := x.(map[string]any); isMap {
			*out = append(*out, m)
		}
	}
	return true
}

func decodeObject(raw string, out *map[string]any) bool {
	var v any
	if decodeNumbers([]byte(raw), &v) != nil {
		return false
	}
	m, ok := v.(map[string]any)
	*out = m
	return ok
}

// kindOf is burnrate.kind_of: (model, effort) as configured; the model of another vendor than the default is prefixed with it.
func (e *Env) kindOf(agent string) (string, string, bool) {
	cfg, err := e.agentConfig(agent)
	if err != nil {
		return "", "", false // one agent's bad config is not the estimate's problem
	}
	vendor, model, effort := team.Parts(cfg)
	if vendor != team.DefaultVendor {
		model = vendor + "/" + model
	}
	return model, effort, true
}

// Load is how full an account is with agents, as opposed to with usage.
type Load struct {
	Account string
	Active  int      // agents mid-turn on it
	Cap     int      // most agents mid-turn at once
	Burn    *float64 // percent of the shortest window per second; nil unmeasured
	Costs   *Costs
}

// AtCap: the account has as many agents mid-turn as it may.
func (l *Load) AtCap() bool { return l.Active >= l.Cap }

const activeSQL = "SELECT agent, state, closed_at, (SELECT w.account FROM events w WHERE w.agent = agent_state.agent " +
	"AND w.kind = 'wake_reserved' AND w.ts > ? AND w.id > COALESCE((SELECT MAX(s.id) FROM events s " +
	"WHERE s.agent = agent_state.agent AND s.kind IN ('Stop', 'wake_failed')), 0) " +
	"ORDER BY w.id DESC LIMIT 1) AS reserved FROM agent_state WHERE reserved IS NOT NULL OR " +
	"(closed_at IS NULL AND (state = 'RUNNING' OR " +
	"EXISTS (SELECT 1 FROM events m WHERE m.agent = agent_state.agent " +
	"AND m.kind = 'moved_account' AND m.id > COALESCE((SELECT MAX(s.id) FROM events s " +
	"WHERE s.agent = agent_state.agent AND s.kind = 'Stop'), 0))))"

// ActiveByAccount is load.active_by_account: the agents mid-turn, by the account each is running on. An agent just moved counts from
// the move until its next Stop, and a woken one from its wake_reserved event on the account reserved, unless it is seen running.
func (e *Env) ActiveByAccount(q db.Q) (map[string][]string, error) {
	rows, err := q.Query(activeSQL, int64(e.now())-WakeReserveS)
	if err != nil {
		return nil, err
	}
	type row struct {
		agent, state, reserved string
		closed                 bool
	}
	var got []row
	for rows.Next() {
		var agent, state string
		var closedAt sql.NullInt64
		var reserved sql.NullString
		if err := rows.Scan(&agent, &state, &closedAt, &reserved); err != nil {
			rows.Close()
			return nil, err
		}
		got = append(got, row{agent, state, reserved.String, closedAt.Valid && closedAt.Int64 != 0})
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, err
	}
	rows.Close()
	found := map[string][]string{}
	for _, r := range got {
		running := r.state == "RUNNING" && !r.closed
		acct := r.reserved
		if running || r.reserved == "" {
			a, err := team.CurrentAccount(q, r.agent)
			if err != nil {
				return nil, err
			}
			acct = a
		}
		if acct != "" {
			found[acct] = append(found[acct], r.agent)
		}
	}
	return found, nil
}

// ActiveAgents is load.active_agents: how many agents are mid-turn on each account, exclude left out.
func (e *Env) ActiveAgents(q db.Q, exclude string) (map[string]int, error) {
	by, err := e.ActiveByAccount(q)
	if err != nil {
		return nil, err
	}
	out := map[string]int{}
	for a, names := range by {
		n := 0
		for _, x := range names {
			if x != exclude {
				n++
			}
		}
		if n > 0 {
			out[a] = n
		}
	}
	return out, nil
}

// ReadAll is load.read_all: every account's load; exclude leaves one agent out of the counts, the one being placed.
func (e *Env) ReadAll(q db.Q, exclude string) (map[string]*Load, error) {
	active, err := e.ActiveAgents(q, exclude)
	if err != nil {
		return nil, err
	}
	costs, err := e.LoadCosts(q)
	if err != nil {
		return nil, err
	}
	out := map[string]*Load{}
	for _, a := range paths.ListAccounts(e.Home) {
		l := &Load{Account: a, Active: active[a], Cap: e.CapNow(a), Costs: costs}
		if b, ok := e.ReadBurn(a); ok {
			l.Burn = &b
		}
		out[a] = l
	}
	return out, nil
}
