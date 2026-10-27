package usage

import (
	"math"

	"github.com/handline/handline/go/internal/db"
)

// An account's weekly pace: a token bucket on the week, refilled linearly (the week's quota is spent as the week passes) with a 10% burst
// on top. The tokens an account has order the accounts a move picks from. Its cap shrinks for the risk of a cut-off (CutoffShare) and,
// with pacing on, for running ahead of its pace line (PaceOf).
const (
	WeekS     = 7 * 86400
	PaceBurst = 0.10 // the share of the week's quota that may be spent ahead of the clock
)

// Headroom is the tokens in the account's tightest bucket, the 5h and the weekly window each judged against its own fill rate: how far
// the window has run, plus the burst, less what is spent, as a share of its quota. Positive is room; zero or less is ahead of the window.
// ok is false when neither figure can be acted on (unread, stale beyond carrying, or rolled over), in which case the pace says nothing.
func Headroom(st *AccountStatus, now int64) (float64, bool) {
	most, ok := math.Inf(1), false
	bucket := func(used *int, reset int64, length float64) {
		if used == nil || reset == 0 || length <= 0 {
			return
		}
		elapsed := 1 - math.Min(1, math.Max(0, float64(reset-now)/length))
		most, ok = math.Min(most, elapsed+PaceBurst-float64(*used)/100), true
	}
	bucket(st.ShortTrusted(), st.Short.Reset, float64(st.Short.Minutes*60))
	bucket(st.LongTrusted(), st.Long.Reset, WeekS)
	if !ok {
		return 0, false
	}
	return most, true
}

// FiveHourShare is how many times over the account's shortest window lasts to its reset at its burn (percent of that window per second)
// plus extra, one more agent's: 1 or more outlasts the reset, less hits the wall first. ok false where nothing trusted says so (a stale or
// unread figure, no reset), which holds and moves nothing: a start is never held on a guess.
func FiveHourShare(st *AccountStatus, burn, extra, now float64) (share float64, ok bool) {
	used, resetIn := st.ShortTrusted(), float64(st.Short.Reset)-now
	if used == nil || st.Short.Reset == 0 || resetIn <= 0 {
		return 1, false
	}
	if rate := burn + extra; rate > 0 {
		return float64(max(0, ShortBlock-*used)) / rate / resetIn, true
	}
	return 1, true
}

// CutoffShare is the share of its current burn (percent of the shortest window per second, converted by longPerShort) the account can keep
// up until the week resets without reaching its block: 1 or more is no risk of an agent being cut off mid-work. The shortest window is
// not judged here: a start is held on it (FiveHourShare). 1 with no burn or no ratio.
func CutoffShare(st *AccountStatus, burn, longPerShort float64, haveRatio bool, now float64) float64 {
	left, ok := WeekEndsIn(st, now)
	used, rate := st.LongTrusted(), burn*longPerShort
	if !ok || !haveRatio || used == nil || rate <= 0 || left <= 0 {
		return 1
	}
	return math.Min(1, float64(max(0, LongBlock-*used))/rate/left)
}

// CutoffCap is the cap shrunk to the share of the burn the account can keep up: all of it with no risk, none with nothing left, and in
// between that share of it (rounded up, so an account with anything left still takes one agent). The founder's cap stays the most.
func CutoffCap(base int, share float64) int {
	switch {
	case share <= 0:
		return 0
	case share >= 1:
		return base
	}
	return int(math.Ceil(float64(base) * share))
}

// CapNow is the cap in force for the account now: the configured one, shrunk only where the account's burn would cut it off before a reset.
func (e *Env) CapNow(q db.Q, acct string) int {
	costs, _ := e.LoadCosts(q) // unread costs judge the shortest window alone
	return e.capWith(acct, costs)
}

// CapUnpaced is CapNow without the pace: what a person's own start and a restart are held to.
func (e *Env) CapUnpaced(q db.Q, acct string) int {
	costs, _ := e.LoadCosts(q)
	return e.cutoffCap(e.config(), acct, e.Read(acct), costs)
}

func (e *Env) capWith(acct string, costs *Costs) int {
	cfg, st := e.config(), e.Read(acct)
	c := e.cutoffCap(cfg, acct, st, costs)
	ps := PaceSettingsOf(cfg)
	if p := PaceOf(st, e.now(), ps); p != nil {
		c = min(c, p.Cap(cfg.CapOf(acct), ps))
	}
	return c
}

func (e *Env) cutoffCap(cfg Config, acct string, st *AccountStatus, costs *Costs) int {
	base := cfg.CapOf(acct)
	burn, ok := e.ReadBurn(acct)
	if !ok {
		return base
	}
	ratio, haveRatio := 0.0, false
	if costs != nil {
		ratio, haveRatio = costs.LongPerShort(acct)
	}
	return CutoffCap(base, CutoffShare(st, burn, ratio, haveRatio, e.now()))
}

// The pacing settings' config.yaml keys and defaults (settings.set).
const (
	PacingKey         = "pacing"
	PaceTargetKey     = "pace_target_pct"
	PaceBurstKey      = "pace_burst_pct"
	DefaultPaceTarget = 98
	DefaultPaceBurst  = 10
)

// PaceSettings is how accounts are paced: On holds them to their line, which ends at Target percent of the week; Burst is how many
// points ahead of it an account may run.
type PaceSettings struct {
	On            bool
	Target, Burst int
}

// PaceSettingsOf reads them from config, each at its default when unset or out of range.
func PaceSettingsOf(cfg Config) PaceSettings {
	ps := PaceSettings{On: cfg[PacingKey] != false, Target: DefaultPaceTarget, Burst: DefaultPaceBurst}
	if n, ok := wholeIn(cfg[PaceTargetKey], 50, 100); ok {
		ps.Target = n
	}
	if n, ok := wholeIn(cfg[PaceBurstKey], 0, 50); ok {
		ps.Burst = n
	}
	return ps
}

func wholeIn(v any, lo, hi int) (int, bool) {
	var n int
	switch x := v.(type) {
	case int:
		n = x
	case int64:
		n = int(x)
	default:
		return 0, false
	}
	return n, n >= lo && n <= hi
}

// The pace states (Account.pace.state).
const (
	PaceBehind = "behind"
	PaceAhead  = "ahead"
	PaceOver   = "over"
	PaceOff    = "off"
)

// Pace is an account's weekly use against its pace line (Account.pace). BackAt is unix seconds, 0 for none.
type Pace struct {
	State  string
	Line   float64
	Used   int
	RateH  float64
	BackAt int64
}

// PaceOf is the account's pace now, judged on its last weekly reading, which holds until its reset even when stale; nil with no reading
// or no reset. The week runs from its reset less 7 days to its end: the reset, or the subscription end when that comes first. The line
// is Target times the share of that week passed.
func PaceOf(st *AccountStatus, now float64, ps PaceSettings) *Pace {
	used := st.Long.Pct
	if used == nil || st.Long.Reset == 0 || float64(st.Long.Reset) <= now {
		return nil
	}
	start := float64(st.Long.Reset - WeekS)
	left, _ := WeekEndsIn(st, now)
	end := now + left
	if end <= start {
		return nil
	}
	target := float64(ps.Target)
	p := &Pace{Used: *used, Line: target * math.Min(1, math.Max(0, (now-start)/(end-start)))}
	if hours := left / 3600; hours > 0 && float64(*used) < target {
		p.RateH = (target - float64(*used)) / hours
	}
	ahead := float64(*used) - p.Line
	switch {
	case !ps.On:
		p.State = PaceOff
	case ahead <= 0:
		p.State = PaceBehind
	case ahead <= float64(ps.Burst):
		p.State = PaceAhead
	default:
		p.State = PaceOver
		// With no further use the line reaches used less the burst at this point of the week, or never: then the week's end.
		back := math.Min(end, start+(float64(*used)-float64(ps.Burst))/target*(end-start))
		p.BackAt = int64(math.Ceil(back))
	}
	return p
}

// Cap is base shrunk for the pace: all of it behind the line (or with pacing off), none past the burst, and in between in step with
// the burst left, at least 1.
func (p *Pace) Cap(base int, ps PaceSettings) int {
	switch p.State {
	case PaceAhead:
		return max(1, int(math.Ceil(float64(base)*(p.Line+float64(ps.Burst)-float64(p.Used))/float64(ps.Burst))))
	case PaceOver:
		return 0
	}
	return base
}

// FiveHourShareWith is the account's FiveHourShare with agent started on it: its recorded burn plus that agent's measured cost
// (Costs.Measured). ok false where there is no burn reading or the cost is only the starting value, so nothing is held on a guess.
func (e *Env) FiveHourShareWith(q db.Q, acct, agent string) (float64, bool) {
	burn, ok := e.ReadBurn(acct)
	if !ok {
		return 1, false
	}
	costs, err := e.LoadCosts(q)
	if err != nil {
		return 1, false
	}
	per, measured := costs.Measured(agent)
	if !measured {
		return 1, false
	}
	return FiveHourShare(e.Read(acct), burn, per, e.now())
}

// PaceNow is the account's pace now under config's settings.
func (e *Env) PaceNow(acct string) *Pace {
	return PaceOf(e.Read(acct), e.now(), PaceSettingsOf(e.config()))
}

// Headroom is the account's headroom now, for ordering accounts by pace: the larger, the slower-paced.
func (e *Env) Headroom(acct string) (float64, bool) { return Headroom(e.Read(acct), int64(e.now())) }
