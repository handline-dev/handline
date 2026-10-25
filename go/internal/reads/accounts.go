package reads

import (
	"encoding/json"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

const movesShown = 20

func windowView(w usage.Window) any {
	if w.Pct == nil {
		return nil
	}
	source := "measured"
	if w.Carried {
		source = "estimated"
	}
	var reset any
	if w.Reset != nil {
		reset = ts(*w.Reset)
	}
	return map[string]any{"used_pct": float64(*w.Pct), "resets_at": reset, "source": source}
}

// accountMoves are the newest moved_account events that name the account, as a Python _moves reads them.
func (e *Env) accountMoves(acct string) []any {
	rows, _ := query(e.DB, "SELECT ts, agent, payload FROM events WHERE kind = 'moved_account' ORDER BY id DESC LIMIT 500")
	out := []any{}
	for _, r := range rows {
		var p map[string]any
		if json.Unmarshal([]byte(r.str("payload")), &p) != nil || p == nil {
			if r.str("payload") != "" {
				continue
			}
			p = map[string]any{}
		}
		from, _ := p["from"].(string)
		to, _ := p["to"].(string)
		if from != acct && to != acct {
			continue
		}
		out = append(out, map[string]any{"at": ts(r.i("ts")), "agent": r.str("agent"), "from": p["from"], "to": to,
			"reason": pyText(p["trigger"])})
		if len(out) == movesShown {
			break
		}
	}
	return out
}

// pyText is str(v or "") for the values a trigger holds.
func pyText(v any) string {
	switch s := v.(type) {
	case nil:
		return ""
	case string:
		return s
	}
	b, _ := json.Marshal(v)
	return string(b)
}

func (e *Env) currentAccount(agent string) string {
	r, _ := queryOne(e.DB, "SELECT account FROM agent_state WHERE agent = ?", agent)
	return r.str("account")
}

// placedOn is the account each agent is on as its view says (AgentView.account), so one at rest past its cache's life is on none; an
// agent with no view yet goes by the account it last ran on.
func (e *Env) placedOn(names []string) map[string]string {
	stored, _ := e.storedViews()
	on := make(map[string]string, len(names))
	for _, n := range names {
		raw, ok := stored[n]
		if !ok {
			on[n] = e.currentAccount(n)
			continue
		}
		var v struct{ Account string }
		json.Unmarshal(raw, &v)
		on[n] = v.Account
	}
	return on
}

func loginWord(in, known bool) string {
	if !known {
		return "unknown"
	}
	if in {
		return "ok"
	}
	return "needs_login"
}

// accountsList is accounts.list: every account with its login, usage windows, agents, recent moves, label, caps and cycle.
func (e *Env) accountsList(_ *rpc.Conn, params map[string]any) (any, error) {
	withUsage := false
	if u, given := params["usage"]; given {
		b, ok := u.(bool)
		if !ok {
			return nil, badParam("usage is true or false", "usage")
		}
		withUsage = b
	}
	v := e.accountView()
	var loads map[string]*usage.Load
	if withUsage {
		var err error
		if loads, err = v.ue.ReadAll(e.DB, ""); err != nil {
			return nil, err
		}
	}
	var accts []any
	for _, a := range paths.ListAccounts(e.Home) {
		acct := v.of(a)
		if withUsage {
			v.addUsage(acct, a, loads[a])
		}
		accts = append(accts, acct)
	}
	if accts == nil {
		accts = []any{}
	}
	return map[string]any{"accounts": accts}, nil
}

// Account is the spec's Account of one account, as accounts.list shows it; false when there is no such account.
func (e *Env) Account(name string) (map[string]any, bool) {
	for _, a := range paths.ListAccounts(e.Home) {
		if a == name {
			return e.accountView().of(a), true
		}
	}
	return nil, false
}

// accountView is what the accounts of one call are read with.
type accountView struct {
	e     *Env
	cfg   usage.Config
	ue    *usage.Env
	names []string
	on    map[string]string // agent -> the account it is on
	now   time.Time
}

func (e *Env) accountView() *accountView {
	cfg := usage.LoadConfig(e.Home)
	names := paths.ListAgents(e.Home)
	return &accountView{e: e, cfg: cfg, ue: &usage.Env{Home: e.Home, LoginState: e.LoginState, Config: func() usage.Config { return cfg }},
		names: names, on: e.placedOn(names), now: time.Now()}
}

func (v *accountView) of(a string) map[string]any {
	e, cfg := v.e, v.cfg
	st := usage.ReadStatus(e.Home, a)
	in, known := e.loginState(a)
	on := []any{}
	for _, n := range v.names {
		if v.on[n] == a {
			on = append(on, n)
		}
	}
	var cap any
	if n, ok := cfg.OwnCap(a); ok {
		cap = n
	}
	var end any
	if d := cfg.EndDate(a); d != "" {
		end = d
	}
	acct := map[string]any{
		"name": a, "login_state": loginWord(in, known), "five_hour": windowView(st.Short), "weekly": windowView(st.Long),
		"agents": on, "moves": e.accountMoves(a), "label": cfg.LabelOf(a), "cap": cap, "cap_effective": v.ue.CapNow(e.DB, a),
		"recover": cfg.RecoverEnabled(a),
		"cycle":   map[string]any{"on": cfg.CycleOn(a), "end": end, "end_is_default": cfg.EndIsDefault(a)}}
	if l := e.loginOf(a); l != nil {
		acct["login"] = l
	}
	acct["stalled"] = stallOf(team.Accounts{Home: e.Home}.Stalled(a))
	return acct
}

// stallOf is Account.stalled: the reason a row shows beside STALLED, the StopFailure error, the turn that showed it and since when;
// nil while the account is usable.
func stallOf(s *team.Stall) any {
	if s == nil {
		return nil
	}
	errType := s.Error
	if errType == "" {
		errType = "authentication_failed"
	}
	return map[string]any{"reason": team.StallWord(errType), "error": errType, "detail": s.Why, "since": ts(s.At)}
}

// addUsage adds what `hl account list` shows beside the windows: the plan, the windows' report, the context figure, the age of the
// read, the runway in words, and whether one more agent may start and why. The plan is the one recorded: no read asks claude.
func (v *accountView) addUsage(acct map[string]any, a string, ld *usage.Load) {
	st := v.ue.Read(a)
	active := 0
	if ld != nil {
		active = ld.Active
	}
	allowed, why := st.TakesWork(active)
	if allowed {
		if bad := usage.Unfit(st, ld, ""); bad != "" {
			allowed, why = false, bad
		}
	}
	var burn *float64
	if b, ok := v.ue.ReadBurn(a); ok {
		burn = &b
	}
	var plan any
	if p, _ := (team.Accounts{Home: v.e.Home}).RecordedSubscriptionType(a); p != "" {
		plan = p
	}
	var ctx, age any
	if st.CtxPct != nil {
		ctx = *st.CtxPct
	}
	if s := st.AgeS(); s != nil {
		age = *s
	}
	acct["plan"], acct["windows"], acct["ctx_pct"], acct["age_s"] = plan, st.WindowReport(), ctx, age
	acct["runway"] = usage.RunwayOf(st, burn, st.NowS(), ld).Describe()
	acct["assignable"], acct["assign_reason"] = allowed, why
	acct["pace"] = paceOf(v.ue.PaceNow(a))
}

// paceOf is Account.pace, nil when the account's week is not known.
func paceOf(p *usage.Pace) any {
	if p == nil {
		return nil
	}
	var back any
	if p.BackAt != 0 {
		back = ts(p.BackAt)
	}
	return map[string]any{"state": p.State, "line_pct": p.Line, "used_pct": p.Used, "rate_pct_h": p.RateH, "back_at": back}
}

// loginOf is the account's latest web login as the accounts topic shows it, nil when none was started. It never has the authorize URL: only the founder's account.login reply does.
func (e *Env) loginOf(acct string) map[string]any {
	if e.Login == nil {
		return nil
	}
	l := e.Login(acct)
	if l == nil {
		return nil
	}
	return map[string]any{"login_id": l.ID, "state": l.State}
}

// statusPick is status.pick: the account the next agent would be placed on (null when none may take work), every candidate's reason,
// and what one more agent costs by kind: what `hl status` prints.
func (e *Env) statusPick(_ *rpc.Conn, _ map[string]any) (any, error) {
	v := e.accountView()
	picked, reasons, err := v.ue.Pick(e.DB, usage.PickOpts{})
	if err != nil {
		return nil, err
	}
	candidates := []any{}
	for _, r := range reasons {
		candidates = append(candidates, map[string]any{"account": r.Account, "reason": r.Why})
	}
	costs, err := v.ue.LoadCosts(e.DB)
	if err != nil {
		return nil, err
	}
	var pick any
	if picked != "" {
		pick = picked
	}
	return map[string]any{"picked": pick, "candidates": candidates, "agent_costs": costs.ByKind(paths.ListAgents(e.Home))}, nil
}
