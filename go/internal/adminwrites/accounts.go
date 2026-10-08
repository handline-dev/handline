package adminwrites

import (
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// account.set_cap and account.set: an account's cap, alias, usage recovery and month cycle, in config.yaml. Both write what a retry writes
// again. account.login, account.remove and settings.trust touch the account's credentials and stay with the Python daemon.

const labelMax = 40

func capParam(p map[string]any) (n int64, given bool, err error) {
	if p["cap"] == nil {
		return 0, false, nil
	}
	n, ok := rpc.AsInt(p["cap"])
	if !ok || n < 1 {
		return 0, false, badParam("cap is a whole number, at least 1", "cap")
	}
	return n, true, nil
}

func flagParam(p map[string]any, key string) (v, given bool, err error) {
	if p[key] == nil {
		return false, false, nil
	}
	b, ok := p[key].(bool)
	if !ok {
		return false, false, badParam(key+" is true or false", key)
	}
	return b, true, nil
}

func (e *Env) accountSetCap(c *rpc.Conn, p map[string]any) (any, error) {
	acct, err := e.accountNamed(p)
	if err != nil {
		return nil, err
	}
	n, given, err := capParam(p)
	if err != nil {
		return nil, err
	}
	if !given {
		return nil, badParam("cap is a whole number, at least 1", "cap")
	}
	cfg, err := e.readConfig()
	if err != nil {
		return nil, refused(err.Error(), "")
	}
	cfg.set("active_cap_"+acct, n)
	if err := cfg.save(); err != nil {
		return nil, refused(err.Error(), "")
	}
	return wrote(c, fmt.Sprintf("%s: at most %d agents mid-turn", acct, n), nil)
}

func (e *Env) accountSet(c *rpc.Conn, p map[string]any) (any, error) {
	acct, err := e.accountNamed(p)
	if err != nil {
		return nil, err
	}
	n, hasCap, err := capParam(p)
	if err != nil {
		return nil, err
	}
	recover, hasRecover, err := flagParam(p, "recover")
	if err != nil {
		return nil, err
	}
	cycleOn, hasCycle, err := flagParam(p, "cycle")
	if err != nil {
		return nil, err
	}
	label, hasLabel := p["label"].(string)
	if p["label"] != nil && (!hasLabel || textsafe.HasUnsafe(strings.TrimSpace(label)) || len([]rune(strings.TrimSpace(label))) > labelMax) {
		return nil, badParam(fmt.Sprintf("label is one plain line of at most %d characters", labelMax), "label")
	}
	label = strings.TrimSpace(label)
	endGiven := false
	var end string
	if v, in := p["cycle_end"]; in {
		endGiven = true
		if v != nil {
			s, ok := v.(string)
			if _, perr := time.Parse("2006-01-02", s); !ok || perr != nil {
				return nil, badParam("cycle_end is a date as YYYY-MM-DD, or null", "cycle_end")
			}
			end = s
		}
	}
	cfg, err := e.readConfig()
	if err != nil {
		return nil, refused(err.Error(), "")
	}
	var said []string
	if hasLabel && label != currentLabel(cfg, acct) {
		if label != "" {
			cfg.set("label_"+acct, label)
			said = append(said, "alias "+label)
		} else {
			cfg.set("label_"+acct, nil)
			said = append(said, "alias cleared")
		}
	}
	if hasCap && !ownCapIs(cfg, acct, n) {
		cfg.set("active_cap_"+acct, n)
		said = append(said, fmt.Sprintf("%s: at most %d agents mid-turn", acct, n))
	}
	if hasRecover && recover != (cfg.get("usage_recover_"+acct) != false) {
		if recover {
			cfg.set("usage_recover_"+acct, nil)
		} else {
			cfg.set("usage_recover_"+acct, false)
		}
		said = append(said, "usage recovery "+map[bool]string{true: "on", false: "off"}[recover])
	}
	if endGiven {
		if end == "" {
			cfg.set("sub_end_"+acct, nil)
			said = append(said, acct+": end day cleared")
		} else {
			cfg.set("month_cycle_"+acct, true)
			cfg.set("sub_end_"+acct, end)
			said = append(said, fmt.Sprintf("%s: last day %s", acct, end))
		}
	}
	if hasCycle {
		if cycleOn && cfg.get("sub_end_"+acct) == nil {
			// The end it would default to is the next anniversary of the subscription's start, which only the account's profile knows.
			return nil, refused("turning the month cycle on needs an end day: give cycle_end (the subscription's start is not read here)", "state")
		}
		cfg.set("month_cycle_"+acct, cycleOn)
		said = append(said, fmt.Sprintf("%s: month cycle %s", acct, map[bool]string{true: "on", false: "off"}[cycleOn]))
	}
	if len(said) > 0 {
		if err := cfg.save(); err != nil {
			return nil, refused(err.Error(), "")
		}
	}
	out := strings.Join(said, "; ")
	if out == "" {
		out = "nothing changed"
	}
	return wrote(c, out, nil)
}

// currentLabel is the alias shown for the account, "" when it is shown under its own name.
func currentLabel(cfg *config, acct string) string {
	if s, ok := cfg.get("label_" + acct).(string); ok && strings.TrimSpace(s) != "" {
		if s != acct {
			return s
		}
	}
	return ""
}

func ownCapIs(cfg *config, acct string, n int64) bool {
	switch v := cfg.get("active_cap_" + acct).(type) {
	case int64:
		return v == n
	case int:
		return int64(v) == n
	}
	return false
}
