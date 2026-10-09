package adminwrites

import (
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/weblogin"
)

// account.set: an account's cap, alias, usage recovery and month cycle, in config.yaml. It writes what a retry writes
// again. account.remove and settings.trust touch the account's credentials and stay with the Python daemon.

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

// account.login starts the vendor's login as a child with no terminal and answers the URL it printed. The code comes later, by
// account.login_code; the end of the login is the accounts topic's. Nothing the child prints is kept but that URL, which is not logged.
func (e *Env) accountLogin(c *rpc.Conn, p map[string]any) (any, error) {
	acct, err := e.accountNamed(p)
	if err != nil {
		return nil, err
	}
	l, err := e.Logins.Start(acct)
	var no *weblogin.Refusal
	if errors.As(err, &no) {
		return nil, refused(no.Reason, "state")
	}
	if err != nil {
		return nil, refused("the login could not be started", "")
	}
	// The URL is not kept in the request record: the first answer carries it, and a retry's is refreshed from the login if it still waits.
	out, err := wrote(c, "login started on "+acct, map[string]any{"login_id": l.ID, "state": l.State})
	if m, ok := out.(map[string]any); ok && err == nil {
		m["url"] = l.URL
	}
	return out, err
}

// refreshLogin gives a retried account.login the login's URL while it still waits, and its state as it is now.
func (e *Env) refreshLogin(_ *rpc.Conn, reply map[string]any) error {
	id, _ := reply["login_id"].(string)
	if l, ok := e.Logins.ByID(id); ok {
		reply["state"] = l.State
		if l.State == weblogin.Waiting {
			reply["url"] = l.URL
		}
	}
	return nil
}

// account.login_cancel kills the login's process group. Cancelling a login that ended is accepted.
func (e *Env) accountLoginCancel(c *rpc.Conn, p map[string]any) (any, error) {
	id, ok := p["login_id"].(string)
	if !ok || id == "" {
		return nil, badParam("login_id is a login's id", "login_id")
	}
	if err := e.Logins.Cancel(id); err != nil {
		return nil, notFound("login", id, "no such login: "+id)
	}
	return wrote(c, "login cancelled", nil)
}
