package usage

import (
	"fmt"
	"math"
	"time"

	"github.com/handline/handline/go/internal/timefmt"
)

// The fail-closed account status (handline/usage/status.py): what a status file says about an account and how far it is trusted. A
// figure nothing can name is not a full tank, it is a refusal to assign. Reading an unknown as 0 makes every account look fresh.

const (
	// StaleAfterS: a snapshot older than this is not trusted as current.
	StaleAfterS = 900
	// ClockSkewGraceS: a future timestamp further out than this is unreadable, not fresh.
	ClockSkewGraceS = 60
	// CarryMaxAgeS is how long a carried figure may stand in for a measurement.
	CarryMaxAgeS = StaleAfterS
	// WallPct is triggers.WALL_PCT: at these nothing can run at all.
	WallPct    = 100
	ShortBlock = WallPct
	LongBlock  = WallPct
	// IdleFloorMarginPct judges a stale floor this far below the wall, to cover spend no collector sees.
	IdleFloorMarginPct = 30
	// UnmeteredWhy is why an account whose vendor reports no usage takes only the agents that name it.
	UnmeteredWhy = "unmetered - its vendor reports no usage; only agents that name it run there"
)

// Source marks, in one place (status.SOURCE_MARK, IDLE_MARK).
var SourceMark = map[string]string{"live": " ", "carried": "~", "carried_old": "!", "quiet": "*", "unknown": " "}

const IdleMark = "?"

// Mark is how a figure's provenance is drawn.
func Mark(src string) string {
	if m, ok := SourceMark[src]; ok {
		return m
	}
	return " "
}

// AcctWindow is one usage window as read back; a nil Pct is unknown, never coerced to 0.
type AcctWindow struct {
	Minutes int
	Pct     *int
	Reset   int64  // 0: none
	At      *int64 // when the figure was observed, not when it was copied
	Carried bool
	// Lost: the collector had a figure it could not carry. That is ignorance, and it must not read as a quiet window.
	Lost bool
	// Readable is false when the window is missing or unreadable (NaN, a string, a bool); an explicit null is readable.
	Readable bool
}

// Label is the window's length as a person says it: 300 -> 5h, 10080 -> 7d.
func (w AcctWindow) Label() string { return WindowLabel(w.Minutes) }

// WindowLabel is windows.label.
func WindowLabel(minutes int) string {
	switch {
	case minutes <= 0:
		return "?"
	case minutes%1440 == 0:
		return fmt.Sprintf("%dd", minutes/1440)
	case minutes%60 == 0:
		return fmt.Sprintf("%dh", minutes/60)
	}
	return fmt.Sprintf("%dm", minutes)
}

// AccountStatus is status.AccountStatus.
type AccountStatus struct {
	Account   string
	Exists    bool
	Unmetered bool
	Raw       map[string]any
	Error     string // "" none

	Short, Long AcctWindow
	CtxPct      *int
	CtxUsed     *int64
	CostUSD     *float64
	SessionID   string
	Transcript  string
	ModelID     string
	CollectedAt *int64
	// AsOf is the moment ages are judged against; nil is the clock. A pure reader or a replay of a past tick sets it.
	AsOf *float64
	// Clock is the wall clock behind a nil AsOf, in unix seconds.
	Clock func() float64
	// SubEnd is when a one-month subscription stops being usable, 0 when none; LastWeekFirst spends the account first while its last
	// week is short.
	SubEnd        float64
	LastWeekFirst bool
}

func (s *AccountStatus) now() float64 {
	if s.AsOf != nil {
		return *s.AsOf
	}
	if s.Clock != nil {
		return s.Clock()
	}
	return float64(time.Now().UnixNano()) / 1e9
}

// NowS is the moment this status is judged at.
func (s *AccountStatus) NowS() float64 { return s.now() }

// AgeS is the snapshot's age, nil when it cannot be trusted: a timestamp in the future means a skewed clock or wrong units, and
// clamping that to 0 would look permanently fresh.
func (s *AccountStatus) AgeS() *float64 {
	if s.CollectedAt == nil {
		return nil
	}
	age := s.now() - float64(*s.CollectedAt)
	if age < -ClockSkewGraceS {
		return nil
	}
	age = math.Max(0, age)
	return &age
}

// Stale: no trustworthy age, or older than StaleAfterS.
func (s *AccountStatus) Stale() bool {
	age := s.AgeS()
	return age == nil || *age > StaleAfterS
}

// ShortAgeS is how long ago the shortest window's figure was observed.
func (s *AccountStatus) ShortAgeS() *float64 {
	if s.Short.At == nil {
		return nil
	}
	age := s.now() - float64(*s.Short.At)
	if age < -ClockSkewGraceS {
		return nil
	}
	age = math.Max(0, age)
	return &age
}

// IsLiveReading: a figure this payload reported, timestamped, not carried: the only kind a rate can be measured from.
func (s *AccountStatus) IsLiveReading() bool {
	return s.ShortSource() == "live" && s.Short.Pct != nil && s.Short.At != nil && *s.Short.At != 0
}

// ShortSource is which of the five cases this reading is: live, carried, carried_old, quiet, unknown. The window's `at`, not the
// flag, bounds the carry: a poll gone quiet for hours must not pass for fresh because something else still refreshes collected_at.
func (s *AccountStatus) ShortSource() string {
	if !s.Exists || s.Error != "" || s.Stale() || !s.Short.Readable {
		return "unknown"
	}
	if s.Short.Lost {
		return "unknown"
	}
	if s.Short.Pct == nil {
		return "quiet"
	}
	age := s.ShortAgeS()
	if age != nil && *age > CarryMaxAgeS {
		return "carried_old"
	}
	if s.Short.Carried {
		if age == nil {
			return "carried_old"
		}
		return "carried"
	}
	return "live"
}

// ShortTrusted is what may be acted on, nil when genuinely unknown. A quiet window reads as 0: a reading with no such window is a
// positive observation that nothing was spent in it.
func (s *AccountStatus) ShortTrusted() *int {
	switch src := s.ShortSource(); src {
	case "unknown":
		return nil
	case "quiet":
		zero := 0
		return &zero
	}
	return s.Short.Pct
}

// LongSource is where the longest window's figure comes from, on the same terms. Once its reset has passed it describes a window
// that no longer exists.
func (s *AccountStatus) LongSource() string {
	if !s.Exists || s.Error != "" || s.Long.Lost || s.Long.Pct == nil {
		return "unknown"
	}
	now := int64(s.now())
	if s.Long.Reset != 0 && s.Long.Reset <= now {
		return "unknown"
	}
	var age *int64
	if s.Long.At != nil {
		a := now - *s.Long.At
		age = &a
	}
	if age != nil && *age > CarryMaxAgeS {
		return "carried_old"
	}
	if s.Stale() || s.Long.Carried {
		if age == nil {
			return "carried_old"
		}
		return "carried"
	}
	return "live"
}

// LongTrusted is the weekly figure that may be acted on: a floor, or nil.
func (s *AccountStatus) LongTrusted() *int {
	if s.LongSource() == "unknown" {
		return nil
	}
	return s.Long.Pct
}

// TrustedCtxPct and TrustedCtxUsed are nil while the snapshot is stale.
func (s *AccountStatus) TrustedCtxPct() *int {
	if s.Stale() {
		return nil
	}
	return s.CtxPct
}

func (s *AccountStatus) TrustedCtxUsed() *int64 {
	if s.Stale() {
		return nil
	}
	return s.CtxUsed
}

// LastKnown is the figures of a stale snapshot with their age, for a reader. Display only: nothing that decides reads it.
func (s *AccountStatus) LastKnown() string {
	if !s.Exists || s.Error != "" || s.Short.Pct == nil {
		return ""
	}
	now := s.now()
	age := "unknown age"
	if a := s.AgeS(); a != nil {
		age = timefmt.Ago(int64(*a)) + " old"
	}
	gone := ""
	if s.Short.Reset != 0 && float64(s.Short.Reset) <= now {
		gone = ", reset since"
	}
	text := fmt.Sprintf("%s %d%% (%s%s)", s.Short.Label(), *s.Short.Pct, age, gone)
	if s.Long.Pct != nil {
		weekGone := ""
		if s.Long.Reset != 0 && float64(s.Long.Reset) <= now {
			weekGone = " (reset since)"
		}
		text += fmt.Sprintf(", %s %d%%%s", s.Long.Label(), *s.Long.Pct, weekGone)
	}
	return text
}

// Assignable: may new work be given to this account? active is agents of ours already mid-turn on it, which only matters to a stale
// reading (IdleFloor).
func (s *AccountStatus) Assignable(active int) (bool, string) {
	switch {
	case s.Unmetered:
		return false, UnmeteredWhy
	case !s.Exists:
		return false, "no status.json - no session has run yet (fail-closed)"
	case s.Error != "":
		return false, fmt.Sprintf("status.json unreadable: %s (fail-closed)", s.Error)
	}
	if s.Stale() {
		if ok, why := s.IdleFloor(active); why != "" {
			return ok, why
		}
		week := s.Long.Pct
		if week != nil && *week >= LongBlock && !s.Long.Lost && !(s.Long.Reset != 0 && float64(s.Long.Reset) <= s.now()) {
			back := ""
			if s.Long.Reset != 0 {
				back = " - resets " + timefmt.Stamp(s.Long.Reset, timefmt.Short)
			}
			return false, fmt.Sprintf("%s %d%% at last reading%s", s.Long.Label(), *week, back)
		}
		shown := "unknown"
		if a := s.AgeS(); a != nil {
			shown = timefmt.Ago(int64(*a))
		}
		return false, fmt.Sprintf("nothing has run on it for %s - the snapshot is stale (fail-closed)", shown)
	}
	pct := s.ShortTrusted()
	if pct == nil {
		return false, fmt.Sprintf("%s usage unknown (fail-closed)", s.Short.Label())
	}
	if *pct >= ShortBlock {
		return false, fmt.Sprintf("%s usage %d%% >= %d%%", s.Short.Label(), *pct, ShortBlock)
	}
	if week := s.LongTrusted(); week != nil && *week >= LongBlock {
		return false, fmt.Sprintf("%s usage %d%% >= %d%%", s.Long.Label(), *week, LongBlock)
	}
	if s.Long.Lost {
		return false, fmt.Sprintf("%s usage was lost - the longest window's gate cannot be checked (fail-closed)", s.Long.Label())
	}
	switch s.ShortSource() {
	case "carried_old":
		shown := "unknown"
		if a := s.ShortAgeS(); a != nil {
			shown = fmt.Sprintf("%dm", int(*a/60))
		}
		return false, fmt.Sprintf("%s %d%% was observed %s ago and only ever rises (carried past %ds)", s.Short.Label(), *pct, shown, CarryMaxAgeS)
	case "quiet":
		return true, fmt.Sprintf("%s window quiet - nothing spent in it (inferred 0%%)", s.Short.Label())
	case "carried":
		return true, fmt.Sprintf("%s %d%% (carried, still inside the window)", s.Short.Label(), *pct)
	}
	return true, fmt.Sprintf("%s %d%%", s.Short.Label(), *pct)
}

// TakesWork: may a session be started here: assignable, or a rolled-over stale account, and under the weekly wall. The usage half of
// the question; Unfit adds the cap and the stretch.
func (s *AccountStatus) TakesWork(active int) (bool, string) {
	ok, why := s.Assignable(active)
	week := s.LongTrusted()
	if rolled, rwhy := s.RolledOver(); !ok && rolled {
		// Its weekly figure is a floor unless the week itself has passed.
		ok, why = true, rwhy
		if s.Long.Reset != 0 && float64(s.Long.Reset) <= s.now() {
			week = nil
		} else {
			week = s.Long.Pct
		}
	}
	if ok && week != nil && *week >= LongBlock {
		return false, fmt.Sprintf("%s usage %d%% >= %d%%", s.Long.Label(), *week, LongBlock)
	}
	return ok, why
}

// RolledOver: a stale account whose shortest window has passed can take work if its last weekly reading had room: the session that
// starts there writes a fresh reading, and the move trigger takes the agent off if it is full.
func (s *AccountStatus) RolledOver() (bool, string) {
	if !s.Exists || s.Error != "" || s.Long.Lost || !s.Stale() || s.AgeS() == nil {
		return false, ""
	}
	now := s.now()
	var passed bool
	if s.Short.Reset != 0 {
		passed = float64(s.Short.Reset) <= now
	} else {
		passed = s.Short.Minutes != 0 && *s.AgeS() >= float64(s.Short.Minutes*60)
	}
	if !passed {
		return false, ""
	}
	week := s.Long.Pct
	weekOver := s.Long.Reset != 0 && float64(s.Long.Reset) <= now
	if week == nil && !weekOver {
		return false, ""
	}
	if week != nil && *week >= LongBlock && !weekOver {
		return false, ""
	}
	if last := s.LastKnown(); last != "" {
		return true, last + " - stale, window rolled over"
	}
	shown := "its week has rolled over"
	if !weekOver {
		shown = fmt.Sprintf("%s %d%%", s.Long.Label(), *week)
	}
	return true, "stale, window rolled over, " + shown
}

// IdleFloor: a stale account still inside its shortest window with no agent of ours mid-turn on it is idle, not unknown, and keeps
// its floor. A floor is a lower bound, so it is judged IdleFloorMarginPct below the wall, never against the wall itself. The reason
// is "" when this rule does not apply.
func (s *AccountStatus) IdleFloor(active int) (bool, string) {
	if !s.Exists || s.Error != "" || s.Short.Lost || !s.Stale() || s.Short.Pct == nil || !s.Short.Readable || active > 0 {
		return false, ""
	}
	now := s.now()
	// Well-formed data cannot have its reset ahead of us while the snapshot is older than a window is wide: only a rewound clock or a
	// hand-edited file gets here, and that is unknown.
	if a := s.AgeS(); a != nil && *a >= float64(s.Short.Minutes*60) {
		return false, ""
	}
	if s.Short.Reset == 0 || float64(s.Short.Reset) <= now {
		return false, "" // rolled_over's case
	}
	text := s.LastKnown()
	if text == "" {
		text = fmt.Sprintf("%s %d%%", s.Short.Label(), *s.Short.Pct)
	}
	if *s.Short.Pct >= ShortBlock-IdleFloorMarginPct {
		return false, fmt.Sprintf("%s when last seen - too close to %d%% to start on a stale reading", text, ShortBlock)
	}
	return true, text + " - stale but idle"
}

// ShownShort is what the shortest window's column draws: the trusted figure and its mark, else the floor an idle account was last
// seen at, marked "?".
func (s *AccountStatus) ShownShort() (*int, string) {
	if t := s.ShortTrusted(); t != nil {
		return t, Mark(s.ShortSource())
	}
	if s.Exists && s.Error == "" && !s.Short.Lost && s.Stale() && s.Short.Pct != nil && s.Short.Readable &&
		s.Short.Reset != 0 && float64(s.Short.Reset) > s.now() {
		return s.Short.Pct, IdleMark
	}
	return nil, " "
}

// WindowReport is the shortest and, when the vendor has one, the longest window as a machine reader wants them.
func (s *AccountStatus) WindowReport() []map[string]any {
	var out []map[string]any
	for i, w := range []AcctWindow{s.Short, s.Long} {
		trusted, source := s.ShortTrusted(), s.ShortSource()
		if i == 1 {
			if w.Minutes == 0 {
				continue
			}
			trusted, source = s.LongTrusted(), s.LongSource()
		}
		var reset any
		if w.Reset != 0 {
			reset = w.Reset
		}
		out = append(out, map[string]any{"window": w.Label(), "minutes": w.Minutes, "pct": ptrAny(w.Pct), "effective": ptrAny(trusted),
			"source": source, "reset": reset})
	}
	return out
}

func ptrAny(p *int) any {
	if p == nil {
		return nil
	}
	return *p
}

// ShortHeadroom is the remaining headroom in the shortest window in percent, nil when unknown.
func (s *AccountStatus) ShortHeadroom() *int {
	pct := s.ShortTrusted()
	if pct == nil {
		return nil
	}
	h := max(0, ShortBlock-*pct)
	return &h
}

// LongHeadroom is the remaining weekly headroom in percent, nil when unknown.
func (s *AccountStatus) LongHeadroom() *int {
	week := s.LongTrusted()
	if week == nil {
		return nil
	}
	h := max(0, LongBlock-*week)
	return &h
}
