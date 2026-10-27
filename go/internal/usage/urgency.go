package usage

import "sort"

// The weekly ranking: an account whose unused week runs out soonest is spent first (T6328). The 5-hour test (FiveHourShare) and the cap
// filter the accounts first; this only orders those that passed.
const (
	// UrgencyBandAbs and UrgencyBandRel: accounts within the larger of this many points per hour and this share of the top score count as
	// similar, and a start goes to the one with the fewest agents mid-turn.
	UrgencyBandAbs = 0.2
	UrgencyBandRel = 0.15
	// RepickGain is how much more urgent (points per hour) another account must be for a closed agent to wake there instead of at home.
	RepickGain = 0.5
)

// Urgency is the account's unused weekly use per hour left before its week ends (the reset, or the subscription's end when that comes
// first). ok false where the weekly reading is stale or unread, or the week is already over: such an account is not ranked.
func Urgency(st *AccountStatus) (float64, bool) {
	used, left := st.LongTrusted(), 0.0
	if used == nil {
		return 0, false
	}
	var ok bool
	if left, ok = WeekEndsIn(st, st.NowS()); !ok || left <= 0 {
		return 0, false
	}
	return float64(max(0, LongBlock-*used)) / (left / 3600), true
}

// Ranked is one account that passed the filters, as the ranking sees it.
type Ranked struct {
	Name    string
	Urgency float64
	Known   bool // Urgency could be read
	Active  int  // agents mid-turn on it
}

// BestByUrgency is the account to start on: of those within the band of the most urgent, the one with the fewest agents mid-turn (then
// the more urgent, then the name); ok false when no account's urgency is known, and the caller ranks by its older key.
func BestByUrgency(c []Ranked) (Ranked, bool) {
	var known []Ranked
	top := 0.0
	for _, r := range c {
		if r.Known {
			known = append(known, r)
			top = max(top, r.Urgency)
		}
	}
	if len(known) == 0 {
		return Ranked{}, false
	}
	sort.SliceStable(known, func(i, j int) bool {
		a, b := known[i], known[j]
		switch {
		case a.InBand(top) != b.InBand(top):
			return a.InBand(top)
		case !a.InBand(top) && a.Urgency != b.Urgency:
			return a.Urgency > b.Urgency
		case a.Active != b.Active:
			return a.Active < b.Active
		case a.Urgency != b.Urgency:
			return a.Urgency > b.Urgency
		}
		return a.Name < b.Name
	})
	return known[0], true
}

// InBand is whether r is similar to the most urgent account, whose score is top.
func (r Ranked) InBand(top float64) bool {
	return r.Known && r.Urgency >= top-max(UrgencyBandAbs, UrgencyBandRel*top)
}
