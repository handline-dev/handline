package usage

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

// Env is what reading and choosing an account depends on that is not the status files: the clock, config.yaml and the lookups other
// packages own. A nil lookup takes the default named on it; every field is replaceable for a test or a replay.
type Env struct {
	Home string
	// Now is unix seconds; nil is the wall clock.
	Now func() float64
	// Config is config.yaml as the readers see it; nil reads it from Home each time.
	Config func() Config
	// Metered is false for an account whose vendor reports no usage. nil: only the claude vendor is metered; another vendor's meter is
	// not ported, so its accounts refuse (fail-closed) rather than read as quiet.
	Metered func(acct string) bool
	// LoginState is whether the account has credentials; known false is a process that cannot tell. nil: team.Accounts{Home}.
	LoginState func(acct string) (loggedIn, known bool)
	// PlanKey is the plan weekly figures are pooled by. nil: the recorded subscription type.
	PlanKey func(acct string) string
	// AgentConfig is an agent's agent.yaml. nil: the roster of Home.
	AgentConfig func(agent string) (team.Config, error)
	// Refresh asks the account fresh and records the answer, reporting whether it did (usage_api.read + record). nil: never asks.
	Refresh func(acct string) bool
}

func (e *Env) now() float64 {
	if e.Now != nil {
		return e.Now()
	}
	return float64(time.Now().UnixNano()) / 1e9
}

// CurrentConfig is config.yaml as the readers see it.
func (e *Env) CurrentConfig() Config { return e.config() }

func (e *Env) config() Config {
	if e.Config != nil {
		return e.Config()
	}
	return LoadConfig(e.Home)
}

func (e *Env) metered(acct string) bool {
	if e.Metered != nil {
		return e.Metered(acct)
	}
	v := team.AccountVendor(e.Home, acct)
	return v == team.DefaultVendor || team.CheckVendor(v) != nil // no driver is left to the login check, which refuses it
}

func (e *Env) loginState(acct string) (bool, bool) {
	if e.LoginState != nil {
		return e.LoginState(acct)
	}
	return team.Accounts{Home: e.Home}.LoginState(acct)
}

func (e *Env) planKey(acct string) string {
	if e.PlanKey != nil {
		return e.PlanKey(acct)
	}
	plan, _ := team.Accounts{Home: e.Home}.RecordedSubscriptionType(acct)
	return plan
}

func (e *Env) agentConfig(agent string) (team.Config, error) {
	if e.AgentConfig != nil {
		return e.AgentConfig(agent)
	}
	return team.NewRoster(e.Home).Read(agent)
}

// asPct and asInt64 are status._as_pct and _as_int: a usage percentage rounds up, a bool or a string is nothing.
func asPctInt(v any) *int { return asPct(v) }

func asInt64(v any) *int64 {
	n, ok := v.(json.Number)
	if !ok {
		if f, isF := v.(float64); isF && !math.IsNaN(f) && !math.IsInf(f, 0) {
			i := int64(f)
			return &i
		}
		return nil
	}
	f, err := n.Float64()
	if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
		return nil
	}
	i := int64(f)
	return &i
}

func acctWindowOf(e map[string]any) AcctWindow {
	pct := asPctInt(e["used_percent"])
	readable := e["readable"] != false && (e["used_percent"] == nil || pct != nil)
	minutes, _ := isInt(e["minutes"])
	var reset int64
	if r := asInt64(e["resets_at"]); r != nil {
		reset = *r
	}
	return AcctWindow{Minutes: minutes, Pct: pct, Reset: reset, At: asInt64(e["observed_at"]), Carried: e["carried"] == true,
		Lost: e["lost"] == true, Readable: readable}
}

// WindowsOf is status.windows_of: the shortest and the longest window a record holds. A vendor with one window has no longest; a
// record with none has no shortest either, and that reads as unreadable, never as a quiet window.
func WindowsOf(raw map[string]any) (short, long AcctWindow) {
	got := entries(raw)
	long = AcctWindow{Readable: true}
	if len(got) == 0 {
		return AcctWindow{}, long
	}
	short = acctWindowOf(got[0])
	if len(got) > 1 {
		long = acctWindowOf(got[len(got)-1])
	}
	return short, long
}

// Read is status.read for an account's own status file.
func (e *Env) Read(acct string) *AccountStatus {
	return e.ReadFile(acct, paths.StatusFile(e.Home, acct))
}

// ReadFile is status.read of a given file.
func (e *Env) ReadFile(acct, path string) *AccountStatus {
	st := &AccountStatus{Account: acct, Clock: e.now, Short: AcctWindow{Readable: true}, Long: AcctWindow{Readable: true}}
	cfg := e.config()
	if end, ok := cfg.EndOf(e.Home, acct, time.Unix(int64(e.now()), 0)); ok {
		st.SubEnd = float64(end.Unix())
	}
	st.LastWeekFirst = cfg["cycle_last_week_first"] != false
	if !e.metered(acct) {
		st.Unmetered = true
		return st
	}
	b, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return st
	}
	st.Exists = true
	if err != nil {
		st.Error = err.Error()
		return st
	}
	var v any
	if err := decodeNumbers(b, &v); err != nil {
		st.Error = err.Error()
		return st
	}
	raw, ok := v.(map[string]any)
	if !ok {
		st.Error = "top level is not an object"
		return st
	}
	st.Raw = raw
	st.Short, st.Long = WindowsOf(raw)
	st.CtxPct = asPctInt(raw["ctx_pct"])
	// The snapshot's own field is the raw usage breakdown, not a total: summed as collect.tokens does, nil when nothing was written,
	// never 0. A bare number is read as it is.
	if m, isMap := raw["ctx_used"].(map[string]any); isMap {
		var total int64
		for _, x := range m {
			if n, isNum := isInt(x); isNum {
				total += int64(n)
			}
		}
		st.CtxUsed = &total
	} else {
		st.CtxUsed = asInt64(raw["ctx_used"])
	}
	st.CollectedAt = asInt64(raw["collected_at"])
	st.SessionID, _ = raw["session_id"].(string)
	st.Transcript, _ = raw["transcript_path"].(string)
	st.ModelID, _ = raw["model_id"].(string)
	if f, isNum := num(raw["cost_usd"]); isNum {
		st.CostUSD = &f
	}
	return st
}

func decodeNumbers(b []byte, v *any) error {
	d := json.NewDecoder(strings.NewReader(string(b)))
	d.UseNumber()
	if err := d.Decode(v); err != nil {
		return err
	}
	if d.More() {
		return errTrailing
	}
	return nil
}

type readError string

func (e readError) Error() string { return string(e) }

const errTrailing = readError("Extra data after the top-level value")

// ReadForAgent is usage as it applies to one agent: rate limits come from the account, context from the agent's own session file. An
// agent on no account has run nothing, so nothing about it is known. An own snapshot that is missing, unreadable or stale leaves the
// context and session unknown, not borrowed from whichever other agent last wrote the account file.
func (e *Env) ReadForAgent(agent, account string) *AccountStatus {
	if account == "" {
		return &AccountStatus{Clock: e.now, Short: AcctWindow{Readable: true}, Long: AcctWindow{Readable: true}}
	}
	st := e.Read(account)
	own := e.ReadFile(account, paths.AgentStatusFile(e.Home, agent))
	if _, err := os.Stat(paths.AgentStatusFile(e.Home, agent)); err != nil || own.Error != "" || own.Stale() {
		st.CtxPct, st.CtxUsed, st.SessionID, st.Transcript, st.ModelID = nil, nil, "", "", ""
		return st
	}
	st.CtxPct, st.CtxUsed, st.SessionID, st.Transcript, st.ModelID = own.CtxPct, own.CtxUsed, own.SessionID, own.Transcript, own.ModelID
	return st
}

// ReportedAccount is the account this agent's own session last rendered its status line under, "" when nothing fresh says. A stale
// snapshot belongs to whichever account was current when the session stopped, not to now.
func (e *Env) ReportedAccount(agent string) string {
	f := paths.AgentStatusFile(e.Home, agent)
	if _, err := os.Stat(f); err != nil {
		return ""
	}
	st := e.ReadFile("", f)
	if st.Error != "" || st.Stale() {
		return ""
	}
	acct, _ := st.Raw["account"].(string)
	return acct
}

// LiveCost is what the sessions we can currently see have spent on this account: the live per-agent snapshots summed. It is a floor
// about the account, not its spend.
func (e *Env) LiveCost(account string) (float64, bool) {
	total, seen := 0.0, false
	for _, agent := range paths.ListAgents(e.Home) {
		f := paths.AgentStatusFile(e.Home, agent)
		if _, err := os.Stat(f); err != nil {
			continue
		}
		st := e.ReadFile(account, f)
		if st.Error != "" || st.Stale() || st.Raw["account"] != account {
			continue
		}
		if st.CostUSD != nil {
			total += *st.CostUSD
			seen = true
		}
	}
	return total, seen
}

// BurnMaxAgeS: a burn reading is the daemon's, taken from agents running on the account; an older one describes a pace nobody keeps.
const BurnMaxAgeS = 600

func burnPath(home, acct string) string {
	return filepath.Join(paths.AccountsDir(home), acct, "burn.json")
}

// WriteBurn records the burn rate the daemon measured (percent per second); at is when it was measured, not when this is written.
func WriteBurn(home, acct string, burn float64, at int64) error {
	p := burnPath(home, acct)
	if st, err := os.Stat(filepath.Dir(p)); err != nil || !st.IsDir() {
		return nil
	}
	b, _ := json.Marshal(map[string]any{"burn_per_s": burn, "at": at})
	return atomicfile.Write(p, string(b))
}

// ReadBurn is the recorded burn in percent per second, or ok false when there is none or it is too old to describe the present.
func (e *Env) ReadBurn(acct string) (float64, bool) {
	b, err := os.ReadFile(burnPath(e.Home, acct))
	if err != nil {
		return 0, false
	}
	var v any
	if decodeNumbers(b, &v) != nil {
		return 0, false
	}
	m, _ := v.(map[string]any)
	at, ok1 := asInt64(m["at"]), true
	burn, ok2 := num(m["burn_per_s"])
	if m == nil || at == nil || !ok1 || !ok2 {
		return 0, false
	}
	if e.now()-float64(*at) > BurnMaxAgeS {
		return 0, false
	}
	return burn, true
}
