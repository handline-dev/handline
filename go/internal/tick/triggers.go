// Package tick is the daemon's tick: what one pass decides for each agent and the passes that follow (session/triggers.py,
// daemon/evaluate.py and the small policies it calls). Everything it touches that another package owns it reaches through the
// interfaces in ports.go; tests use fakes.
package tick

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

// The numbers the triggers and the account pick are made from (session/triggers.py).
const (
	// ContextTokens replaces the cache-expiry trigger: an absolute figure, so it means the same on every model. An agent's own
	// context_tokens overrides it, and ContextClampFraction holds a small-window model down to its share.
	ContextTokens = 150_000
	// ContextClampFraction is the ceiling of last resort for a model whose window is known.
	ContextClampFraction = 0.60
	// WallPct is where both windows are spent to their own ceiling.
	WallPct = 100
	// PollS is how often the daemon looks.
	PollS = 30
	// StayWithinS: an agent at the wall stays parked when its own account resets within this - 13% of a 5h window, the cost of a cold start.
	StayWithinS = 39 * 60
	// MinStretchS is the least stretch of an account's life worth beginning a session for.
	MinStretchS = 30 * 60
	// DefaultActiveCap is the most agents mid-turn on one account at once (Pro); set per account in config.
	DefaultActiveCap = 2
	// MinBurnSpanS: a rate measured over less than this is quantization noise, one point of resolution.
	MinBurnSpanS = 120
	// BurnWindow is how many live samples an account's burn window keeps.
	BurnWindow = 10
)

// TypicalAgentBurnPerS is what one agent working steadily costs an account, in percent of its 5h window per second, until enough has been
// measured: 0.75%/min.
const TypicalAgentBurnPerS = 0.75 / 60

// Trigger is whether an agent should be handed off now, and why.
type Trigger struct {
	Fired  bool
	Kind   string
	Reason string
}

// Sample is one live reading of the shortest window: when it was observed and its percent.
type Sample struct {
	At    int64
	Pct   int
	Known bool // false: the payload carried no percent
}

// BurnRatePerS is percent per second across the whole sample window, 0 when it cannot be measured honestly: too few samples, too short a
// span, or a drop (a reset). Measured end to end, so one quantized step does not dominate the answer.
func BurnRatePerS(samples []Sample) float64 {
	var usable []Sample
	for _, s := range samples {
		if s.Known {
			usable = append(usable, s)
		}
	}
	sort.SliceStable(usable, func(i, j int) bool {
		if usable[i].At != usable[j].At {
			return usable[i].At < usable[j].At
		}
		return usable[i].Pct < usable[j].Pct
	})
	if len(usable) < 2 {
		return 0
	}
	first, last := usable[0], usable[len(usable)-1]
	span := last.At - first.At
	if span < MinBurnSpanS || last.Pct <= first.Pct {
		return 0
	}
	return float64(last.Pct-first.Pct) / float64(span)
}

// Evaluate is whether this agent should hand off now. Usage answers only at the wall; an unreadable figure is not a wall. ctxLimit is a
// token count the caller has already resolved (the agent's own figure or the default, clamped for a known model).
func Evaluate(shortPct, ctxUsed, longPct *int, ctxLimit int) Trigger {
	if shortPct != nil && *shortPct >= WallPct {
		return Trigger{true, "usage", fmt.Sprintf("shortest window %d%% - at the wall", *shortPct)}
	}
	if longPct != nil && *longPct >= WallPct {
		return Trigger{true, "usage_7d", fmt.Sprintf("longest window %d%% - at the wall", *longPct)}
	}
	if ctxUsed != nil && *ctxUsed >= ctxLimit {
		return Trigger{true, "context", fmt.Sprintf("context %s >= %s", commas(*ctxUsed), commas(ctxLimit))}
	}
	short, ctx := "?", "?"
	if shortPct != nil {
		short = strconv.Itoa(*shortPct) + "%"
	}
	if ctxUsed != nil {
		ctx = commas(*ctxUsed)
	}
	return Trigger{Reason: fmt.Sprintf("shortest window %s below the wall, context %s < %s", short, ctx, commas(ctxLimit))}
}

func commas(n int) string {
	s := strconv.Itoa(n)
	neg := strings.HasPrefix(s, "-")
	if neg {
		s = s[1:]
	}
	for i := len(s) - 3; i > 0; i -= 3 {
		s = s[:i] + "," + s[i:]
	}
	if neg {
		s = "-" + s
	}
	return s
}

// StayWithin is T: the measured cost of a cold start as the seconds of the shortest window it spends, when there is a measurement; the
// starting value otherwise.
func StayWithin(coldStartPct *float64, windowS float64) float64 {
	if coldStartPct == nil || *coldStartPct <= 0 || windowS <= 0 {
		return StayWithinS
	}
	return *coldStartPct / 100 * windowS
}

// StayOrMove is, for an agent at the wall, whether a move is worth its cold start. Never for an idle agent - a move is for work that is
// waiting - and not when its own window rolls over sooner than the start would pay back. An unknown reset is further than T.
func StayOrMove(workWaiting bool, resetInS *float64, stayWithin float64) (bool, string) {
	if !workWaiting {
		return false, "no work is waiting - an idle agent does not move"
	}
	if resetInS != nil && *resetInS <= stayWithin {
		return false, fmt.Sprintf("its account resets within %dm - cheaper to wait", int(math.Floor(stayWithin/60)))
	}
	return true, "work is waiting and its account resets too late to wait for"
}

// StretchS is how long an account lasts before its wall with one more agent on it, at that agent's cost.
func StretchS(headroomPct, fillPerS, agentPerS float64) float64 {
	rate := math.Max(0, fillPerS) + agentPerS
	if rate > 0 {
		return headroomPct / rate
	}
	return math.Inf(1)
}

// NextReset is when a window rolls over, used as wake_at for a parked agent: shortly from now when the reset is unknown or past.
func NextReset(reset, now int64) int64 {
	if reset > now {
		return reset
	}
	return now + 300
}

// WakeAfter is when a parked agent should look again: the window its trigger fired on has to roll over, and a trigger on the longest
// outlasts every reset of the shortest.
func WakeAfter(kind string, shortReset, longReset, now int64) int64 {
	if kind == "usage_7d" {
		return NextReset(longReset, now)
	}
	return NextReset(shortReset, now)
}
