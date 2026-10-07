// Package usage is handline/usage's readers for what the accounts screens draw: config.yaml's per-account settings
// (usage/load.py), a status file's windows (usage/windows.py, status.read) and the month cycle (usage/cycle.py).
// Nothing here writes, and no credential is read: the one vendor file touched is claude's `.claude.json`, for the
// subscription's start only, as the Python driver's profile() does.
package usage

import (
	"encoding/json"
	"math"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// DefaultActiveCap is session/triggers.DEFAULT_ACTIVE_CAP.
const DefaultActiveCap = 2

// Config is config.yaml as the readers see it; one that cannot be read is empty.
type Config map[string]any

// LoadConfig reads <home>/config.yaml.
func LoadConfig(home string) Config {
	b, err := os.ReadFile(paths.ConfigFile(home))
	if err != nil {
		return Config{}
	}
	v, err := yamlcompat.Parse(string(b))
	if err != nil {
		return Config{}
	}
	m, _ := v.(map[string]any)
	if m == nil {
		return Config{}
	}
	return m
}

func posInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, n >= 1
	case int64:
		return int(n), n >= 1
	}
	return 0, false
}

// OwnCap is the cap set for this account alone.
func (c Config) OwnCap(acct string) (int, bool) { return posInt(c["active_cap_"+acct]) }

// DefaultCap is the cap of an account with none of its own.
func (c Config) DefaultCap() int {
	if n, ok := posInt(c["active_cap"]); ok {
		return n
	}
	return DefaultActiveCap
}

// CapOf is the cap in force for the account.
func (c Config) CapOf(acct string) int {
	if n, ok := c.OwnCap(acct); ok {
		return n
	}
	return c.DefaultCap()
}

// LabelOf is the name the screens show for an account.
func (c Config) LabelOf(acct string) string {
	if s, ok := c["label_"+acct].(string); ok && strings.TrimSpace(s) != "" {
		return s
	}
	return acct
}

// RecoverEnabled is false only where `usage_recover_<account>: false` says so.
func (c Config) RecoverEnabled(acct string) bool { return c["usage_recover_"+acct] != false }

// Window is one usage window as read back; a nil Pct is unknown.
type Window struct {
	Minutes  int
	Pct      *int
	Reset    *int64
	Carried  bool
	Readable bool
}

// Status is status.read's reading of one account's status file.
type Status struct {
	Short, Long Window
	Error       string
}

func asPct(v any) *int {
	f, ok := num(v)
	if !ok || math.IsNaN(f) || math.IsInf(f, 0) {
		return nil
	}
	n := int(math.Ceil(f))
	return &n
}

func num(v any) (float64, bool) {
	switch n := v.(type) {
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case float64:
		return n, true
	}
	return 0, false
}

func asInt(v any) *int64 {
	f, ok := num(v)
	if !ok || math.IsNaN(f) || math.IsInf(f, 0) {
		return nil
	}
	n := int64(f)
	return &n
}

// isInt is a JSON number written without a fraction or exponent, which is what Python's json reads as an int.
func isInt(v any) (int, bool) {
	n, ok := v.(json.Number)
	if !ok || strings.ContainsAny(n.String(), ".eE") {
		return 0, false
	}
	i, err := n.Int64()
	return int(i), err == nil
}

var legacy = []struct {
	minutes                       int
	pct, reset, at, carried, lost string
}{{300, "pct_5h", "reset_5h", "rl_at", "rl_carried", "rl_lost"}, {10080, "pct_7d", "reset_7d", "rl7_at", "rl7_carried", "rl7_lost"}}

func truthyOne(v any) bool {
	if b, ok := v.(bool); ok {
		return b
	}
	n, ok := v.(json.Number)
	return ok && n.String() == "1"
}

// entries is windows.entries: the record's windows, shortest first, whichever shape it was written in.
func entries(raw map[string]any) []map[string]any {
	got, has := raw["windows"]
	if s, ok := got.(string); ok {
		d := json.NewDecoder(strings.NewReader(s))
		d.UseNumber()
		var v any
		if d.Decode(&v) != nil || d.More() {
			v = nil
		}
		got = v
	}
	if list, ok := got.([]any); ok && (has || got != nil) {
		var out []map[string]any
		for _, e := range list {
			if m, ok := e.(map[string]any); ok {
				if _, ok := isInt(m["minutes"]); ok {
					out = append(out, m)
				}
			}
		}
		sort.SliceStable(out, func(i, j int) bool { a, _ := isInt(out[i]["minutes"]); b, _ := isInt(out[j]["minutes"]); return a < b })
		return out
	}
	any_ := false
	for _, l := range legacy {
		if _, ok := raw[l.pct]; ok {
			any_ = true
		}
	}
	if !any_ {
		return nil
	}
	var out []map[string]any
	for _, l := range legacy {
		e := map[string]any{"minutes": json.Number(itoa(l.minutes)), "used_percent": raw[l.pct], "resets_at": raw[l.reset],
			"carried": truthyOne(raw[l.carried])}
		if _, ok := raw[l.pct]; !ok || raw[l.pct+"_readable"] == false || raw[l.pct+"_readable"] == json.Number("0") {
			e["readable"] = false
		}
		out = append(out, e)
	}
	return out
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func windowOf(e map[string]any) Window {
	pct := asPct(e["used_percent"])
	readable := e["readable"] != false && (e["used_percent"] == nil || pct != nil)
	m, _ := isInt(e["minutes"])
	return Window{Minutes: m, Pct: pct, Reset: asInt(e["resets_at"]), Carried: e["carried"] == true, Readable: readable}
}

// ReadStatus is status.read for a metered account (every vendor's is): a missing file is no reading, a bad one an error.
func ReadStatus(home, acct string) Status {
	st := Status{Short: Window{Readable: true}, Long: Window{Readable: true}}
	b, err := os.ReadFile(paths.StatusFile(home, acct))
	if err != nil {
		return st
	}
	d := json.NewDecoder(strings.NewReader(string(b)))
	d.UseNumber()
	var v any
	if err := d.Decode(&v); err != nil || d.More() {
		st.Error = "unreadable"
		return st
	}
	raw, ok := v.(map[string]any)
	if !ok {
		st.Error = "top level is not an object"
		return st
	}
	got := entries(raw)
	if len(got) == 0 {
		st.Short = Window{}
		return st
	}
	st.Short = windowOf(got[0])
	if len(got) > 1 {
		st.Long = windowOf(got[len(got)-1])
	}
	return st
}

// Started is cycle.started: the moment the subscription began, from claude's profile; absent for any other vendor.
func Started(home, acct string) (time.Time, bool) {
	if team.AccountVendor(home, acct) != "claude" {
		return time.Time{}, false
	}
	dir, err := team.AccountConfigDir(home, acct)
	if err != nil {
		return time.Time{}, false
	}
	b, err := os.ReadFile(dir + "/.claude.json")
	if err != nil {
		return time.Time{}, false
	}
	var doc struct {
		OA struct {
			At any `json:"subscriptionCreatedAt"`
		} `json:"oauthAccount"`
	}
	if json.Unmarshal(b, &doc) != nil {
		return time.Time{}, false
	}
	s, ok := doc.OA.At.(string)
	if !ok || len(s) > 40 {
		return time.Time{}, false
	}
	t, ok := parseISO(s)
	if !ok || t.Unix() == 0 {
		return time.Time{}, false
	}
	return time.Unix(0, int64(float64(t.UnixNano()))).In(time.Local), true
}

func parseISO(s string) (time.Time, bool) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999999", "2006-01-02"} {
		if t, err := time.Parse(layout, strings.Replace(s, "Z", "+00:00", 1)); err == nil {
			return t, true
		}
		if t, err := time.ParseInLocation(layout, s, time.Local); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}
