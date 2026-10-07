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
func (e *Env) accountsList(_ *rpc.Conn, _ map[string]any) (any, error) {
	cfg := usage.LoadConfig(e.Home)
	names := paths.ListAgents(e.Home)
	now := time.Now()
	var accts []any
	for _, a := range paths.ListAccounts(e.Home) {
		st := usage.ReadStatus(e.Home, a)
		in, known := team.Accounts{Home: e.Home}.LoginState(a)
		on := []any{}
		for _, n := range names {
			if e.currentAccount(n) == a {
				on = append(on, n)
			}
		}
		var cap any
		if n, ok := cfg.OwnCap(a); ok {
			cap = n
		}
		var end any
		if d := cfg.EndDate(e.Home, a, now); d != "" {
			end = d
		}
		accts = append(accts, map[string]any{
			"name": a, "login_state": loginWord(in, known), "five_hour": windowView(st.Short), "weekly": windowView(st.Long),
			"agents": on, "moves": e.accountMoves(a), "label": cfg.LabelOf(a), "cap": cap, "cap_effective": cfg.CapOf(a),
			"recover": cfg.RecoverEnabled(a),
			"cycle":   map[string]any{"on": cfg.CycleOn(a), "end": end, "end_is_default": cfg.EndIsDefault(a)}})
	}
	if accts == nil {
		accts = []any{}
	}
	return map[string]any{"accounts": accts}, nil
}
