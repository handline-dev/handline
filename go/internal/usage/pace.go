package usage

import "math"

// An account's weekly pace: a token bucket on the week, refilled linearly (the week's quota is spent as the week passes) with a 10% burst
// on top. The tokens an account has are what it may still spend before it is ahead of its week; its cap shrinks with them.
const (
	WeekS     = 7 * 86400
	PaceBurst = 0.10 // the share of the week's quota that may be spent ahead of the clock
)

// An account may run ahead of the clock by up to the burst and still take an agent: it is ahead of its week only once the burst is spent.
// Headroom is the tokens in the account's bucket, as a share of the week's quota: how far the week has run, plus the burst, less what is
// spent. Positive is room; zero or less is ahead of the week. ok is false when the weekly figure cannot be acted on (unread, stale beyond
// carrying, or its window already rolled over), in which case the pace says nothing and the plain cap stands.
func Headroom(st *AccountStatus, now int64) (float64, bool) {
	used := st.LongTrusted()
	if used == nil || st.Long.Reset == 0 {
		return 0, false
	}
	left := float64(st.Long.Reset-now) / WeekS
	elapsed := 1 - math.Min(1, math.Max(0, left))
	return elapsed + PaceBurst - float64(*used)/100, true
}

// PacedCap is the cap shrunk by the pace: all of it while the bucket holds the burst, none once it is empty, and in between the share the
// bucket holds (rounded up, so a bucket with anything in it still takes one agent). The founder's cap stays the most.
func PacedCap(base int, headroom float64) int {
	switch {
	case headroom <= 0:
		return 0
	case headroom >= PaceBurst:
		return base
	}
	return int(math.Ceil(float64(base) * headroom / PaceBurst))
}

// CapNow is the cap in force for the account now: the configured one, shrunk by the account's weekly pace.
func (e *Env) CapNow(acct string) int {
	base := e.config().CapOf(acct)
	if h, ok := Headroom(e.Read(acct), int64(e.now())); ok {
		return PacedCap(base, h)
	}
	return base
}

// Headroom is the account's headroom now, for ordering accounts by pace: the larger, the slower-paced.
func (e *Env) Headroom(acct string) (float64, bool) { return Headroom(e.Read(acct), int64(e.now())) }
