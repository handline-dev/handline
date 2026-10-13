package usage

import (
	"math"

	"github.com/handline/handline/go/internal/db"
)

// An account's weekly pace: a token bucket on the week, refilled linearly (the week's quota is spent as the week passes) with a 10% burst
// on top. The tokens an account has order the accounts a move picks from; they do not shrink its cap, which only the risk of a cut-off
// does (CutoffShare).
const (
	WeekS     = 7 * 86400
	PaceBurst = 0.10 // the share of the week's quota that may be spent ahead of the clock
)

// Headroom is the tokens in the account's bucket, as a share of the week's quota: how far the week has run, plus the burst, less what is
// spent. Positive is room; zero or less is ahead of the week. ok is false when the weekly figure cannot be acted on (unread, stale beyond
// carrying, or its window already rolled over), in which case the pace says nothing.
func Headroom(st *AccountStatus, now int64) (float64, bool) {
	used := st.LongTrusted()
	if used == nil || st.Long.Reset == 0 {
		return 0, false
	}
	left := float64(st.Long.Reset-now) / WeekS
	elapsed := 1 - math.Min(1, math.Max(0, left))
	return elapsed + PaceBurst - float64(*used)/100, true
}

// CutoffShare is the share of its current burn (percent of the shortest window per second) the account can keep up until each window
// resets without reaching that window's block: 1 or more is no risk of an agent being cut off mid-work. A fast pace alone is no risk.
// longPerShort converts the burn to the longest window, ok false when it is unknown and only the shortest is judged. 1 with no burn.
func CutoffShare(st *AccountStatus, burn, longPerShort float64, haveRatio bool, now float64) float64 {
	share := 1.0
	project := func(used *int, block int, rate, resetIn float64) {
		if used == nil || rate <= 0 || resetIn <= 0 {
			return
		}
		share = math.Min(share, float64(max(0, block-*used))/rate/resetIn)
	}
	if st.Short.Reset != 0 {
		project(st.ShortTrusted(), ShortBlock, burn, float64(st.Short.Reset)-now)
	}
	if left, ok := WeekEndsIn(st, now); ok && haveRatio {
		project(st.LongTrusted(), LongBlock, burn*longPerShort, left)
	}
	return share
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

func (e *Env) capWith(acct string, costs *Costs) int {
	base := e.config().CapOf(acct)
	burn, ok := e.ReadBurn(acct)
	if !ok {
		return base
	}
	ratio, haveRatio := 0.0, false
	if costs != nil {
		ratio, haveRatio = costs.LongPerShort(acct)
	}
	return CutoffCap(base, CutoffShare(e.Read(acct), burn, ratio, haveRatio, e.now()))
}

// Headroom is the account's headroom now, for ordering accounts by pace: the larger, the slower-paced.
func (e *Env) Headroom(acct string) (float64, bool) { return Headroom(e.Read(acct), int64(e.now())) }
