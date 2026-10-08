// Package subscriptions is two upkeep sweeps over the accounts, each read from state every tick (handline/daemon/subscriptions.py).
//
// A month-cycle account whose end date is within `cycle_warn_days` tells the founder once, as a task of the founder's on the board,
// remembered by account and end date: a later end date is a new subscription and is told again.
//
// Each account's plan is asked of its vendor again once an hour, one account at a time and off the tick, because `claude auth status`
// is a process with a ten-second timeout and the plan is recorded on disk for the readers that must not run one. A plan that differs
// from the one seen last tick (Pro to Max) is an event, and reaches the pick and the recovery estimates on the next read.
package subscriptions

import (
	"database/sql"
	"fmt"
	"math"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

// PlanRefreshS is how often an account's plan is asked of its vendor again.
const PlanRefreshS = 3600

// Sender opens a decision for the founder when no agent is on the account.
const Sender = "daemon"

// Asker is team/ask.py's raise_decision: a task of the founder's on the board, opened by agent; the request's id.
type Asker interface {
	RaiseDecision(q db.Q, agent, text, reason string) (int64, error)
}

// Tasks is actions.mark_task_done as the founder's own card: the task marked done with this reason.
type Tasks interface {
	MarkDoneByFounder(q db.Q, tid int64, reason string) (ok bool, msg string, err error)
}

// Plans is the account's plan, from disk and from its vendor.
type Plans interface {
	// Recorded is the plan already recorded for the account, read from disk alone.
	Recorded(acct string) (string, bool)
	// Refresh asks the vendor again and records what it says; "" when none is recorded and none was read.
	Refresh(acct string) (string, error)
}

// Env is what the sweeps work on.
type Env struct {
	DB    db.Q
	Home  string
	Asker Asker
	Tasks Tasks
	Plans Plans
	Now   func() time.Time
	Log   func(format string, a ...any)

	mu     sync.Mutex
	asking bool
	wg     sync.WaitGroup
}

func (e *Env) now() time.Time {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now()
}

func (e *Env) log(format string, a ...any) {
	if e.Log != nil {
		e.Log(format, a...)
	}
}

func told(acct string) string { return "sub_ending_told:" + acct }
func task(acct string) string { return "sub_ending_task:" + acct }

// SweepEndings acts only on what is known. A file that cannot be read this tick (config.yaml caught half written, the profile
// mid-rewrite) or a date that does not parse is not "the cycle is off": the founder's notice stays as it is.
func (e *Env) SweepEndings() error {
	cfg, ok := usage.LoadConfigOrNil(e.Home)
	if !ok {
		e.log("  subscriptions: config.yaml unreadable - notices left as they are")
		return nil
	}
	now := e.now()
	ahead := time.Duration(cfg.WarnDays()) * 24 * time.Hour
	for _, acct := range paths.ListAccounts(e.Home) {
		toldDay, _, err := db.Meta(e.DB, told(acct))
		if err != nil {
			return err
		}
		if !cfg.CycleOn(acct) {
			if err := e.settle(acct, "the month cycle was turned off"); err != nil {
				return err
			}
			if err := db.ClearMeta(e.DB, told(acct)); err != nil { // turned on again, the same date is news again
				return err
			}
			continue
		}
		if toldDay != "" {
			over, ok := usage.DayEnd(toldDay)
			if !ok {
				return fmt.Errorf("invalid isoformat string: %q", toldDay)
			}
			if !over.After(now) {
				if err := e.settle(acct, "the subscription ended; the account is shown as ended"); err != nil {
					return err
				}
			}
		}
		end, ok := cfg.EndOf(e.Home, acct, now)
		if !ok {
			continue
		}
		day := cfg.EndDate(e.Home, acct, now)
		if !now.Before(end) {
			if err := e.settle(acct, "the subscription ended; the account is shown as ended"); err != nil {
				return err
			}
			continue
		}
		if day != toldDay {
			if err := e.settle(acct, "the account's end date changed"); err != nil {
				return err
			}
		}
		if end.Sub(now) > ahead || toldDay == day {
			continue
		}
		// Python's float floor division: the hours print with a decimal.
		hours := strconv.FormatFloat(math.Floor(end.Sub(now).Seconds()/3600), 'f', 1, 64)
		opener, err := e.opener(acct)
		if err != nil {
			return err
		}
		rid, err := e.Asker.RaiseDecision(e.DB, opener, fmt.Sprintf(
			"%s subscription ends %s\n\n"+
				"Account %s's one-month subscription ends %s (its last usable day), in "+
				"%s h. After that it is shown as ended and no agent is placed on it; "+
				"it is not removed. To keep it, resubscribe and run "+
				"`hl account cycle %s --end YYYY-MM-DD`; to let it end, do nothing. "+
				"This task closes by itself once the account has ended or its end date changes.", acct, day, acct, day, hours, acct),
			"subscription ending")
		if err != nil {
			return err
		}
		var taskID int64
		switch err := e.DB.QueryRow("SELECT task_id FROM task_requests WHERE request_id = ?", rid).Scan(&taskID); {
		case err == nil:
			if err := db.SetMeta(e.DB, task(acct), strconv.FormatInt(taskID, 10)); err != nil {
				return err
			}
		case !isNoRows(err):
			return err
		}
		if err := db.SetMeta(e.DB, told(acct), day); err != nil {
			return err
		}
		e.log("  %s: subscription ends %s - founder told", acct, day)
	}
	return nil
}

// settle closes the founder's notice for this account: it is about an end date that has passed or no longer holds. The daemon marks
// it done as the founder's own card, and says so in the reason.
func (e *Env) settle(acct, why string) error {
	tid, _, err := db.Meta(e.DB, task(acct))
	if err != nil || tid == "" {
		return err
	}
	n, err := strconv.ParseInt(tid, 10, 64)
	if err != nil {
		return fmt.Errorf("invalid literal for int() with base 10: %q", tid)
	}
	ok, msg, err := e.Tasks.MarkDoneByFounder(e.DB, n, "closed by the daemon: "+why)
	if err != nil {
		return err
	}
	if err := db.ClearMeta(e.DB, task(acct)); err != nil {
		return err
	}
	if ok {
		e.log("  %s: subscription notice T%s closed (%s)", acct, tid, why)
	} else {
		e.log("  %s: notice T%s: %s", acct, tid, msg)
	}
	return nil
}

// opener is whom the founder's task is opened by: an agent on the account, else any agent, since the board and the home count are
// drawn per listed agent.
func (e *Env) opener(acct string) (string, error) {
	names := paths.ListAgents(e.Home)
	for _, n := range names {
		account, err := team.CurrentAccount(e.DB, n)
		if err != nil {
			return "", err
		}
		if account == acct {
			return n, nil
		}
	}
	if len(names) > 0 {
		return names[0], nil
	}
	return Sender, nil
}

func (e *Env) ask(acct string) {
	defer e.wg.Done()
	defer func() { e.mu.Lock(); e.asking = false; e.mu.Unlock() }()
	plan, err := e.Plans.Refresh(acct)
	switch {
	case err != nil: // a plan not read now is asked again next hour
		e.log("  %s: plan question failed: %s", acct, err)
	case plan == "":
		e.log("  %s: no plan recorded and none read (not logged in, or auth status failed)", acct)
	}
}

// SweepPlans notes a plan that changed, and starts the hourly question to the vendor for the account asked longest ago.
func (e *Env) SweepPlans() error {
	now := e.now().Unix()
	accts := paths.ListAccounts(e.Home)
	for _, acct := range accts {
		plan, _ := e.Plans.Recorded(acct)
		seen, _, err := db.Meta(e.DB, "plan_seen:"+acct)
		if err != nil {
			return err
		}
		if plan != "" && plan != seen {
			if err := db.SetMeta(e.DB, "plan_seen:"+acct, plan); err != nil {
				return err
			}
			if seen != "" {
				if _, err := db.AddEventFor(e.DB, acct, acct, "plan_changed", db.KV{K: "from", V: seen}, db.KV{K: "to", V: plan}); err != nil {
					return err
				}
				e.log("  %s: plan %s -> %s", acct, seen, plan)
			}
		}
	}
	e.mu.Lock()
	busy := e.asking
	e.mu.Unlock()
	if busy {
		return nil
	}
	type due struct {
		at   int64
		acct string
	}
	var order []due
	for _, a := range accts {
		at, err := db.Cursor(e.DB, "plan_checked:"+a)
		if err != nil {
			return err
		}
		order = append(order, due{at, a})
	}
	sort.Slice(order, func(i, j int) bool {
		if order[i].at != order[j].at {
			return order[i].at < order[j].at
		}
		return order[i].acct < order[j].acct
	})
	if len(order) == 0 || now-order[0].at < PlanRefreshS {
		return nil
	}
	if err := db.SetCursor(e.DB, "plan_checked:"+order[0].acct, now); err != nil {
		return err
	}
	e.mu.Lock()
	e.asking = true
	e.mu.Unlock()
	e.wg.Add(1)
	go e.ask(order[0].acct)
	return nil
}

// WaitForPlanAsk lets the plan question the last tick started finish (tests).
func (e *Env) WaitForPlanAsk() { e.wg.Wait() }

// Sweep runs both sweeps.
func (e *Env) Sweep() error {
	if err := e.SweepEndings(); err != nil {
		return err
	}
	return e.SweepPlans()
}

func isNoRows(err error) bool { return err == sql.ErrNoRows }
