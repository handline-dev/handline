// Package staymove is the choice an agent at its usage wall faces: wait on its own account or move to another. It is one pure
// function over the facts the caller gathered (Situation), so a scenario is a struct literal and a test needs no database or daemon.
// Where it moves to is not here: the caller's room test picks the account.
package staymove

import (
	"fmt"
	"math"
)

// DefaultStayWithinS is T before a cold start was ever measured: 13% of a 5h window, a guess at what one costs.
const DefaultStayWithinS = 39 * 60

// Situation is what the choice reads.
type Situation struct {
	WorkWaiting  bool     // a request is queued for it, or a wake is starting it
	ResetInS     *float64 // until its own account's window rolls over; nil when unknown
	ColdStartPct *float64 // the measured share of the window a cold start spends; nil when never measured
	WindowS      float64  // that window's length
	// TurnEndedS is how long ago its session's last turn ended, nil when unknown; CacheLifeS is how long the prompt cache lives.
	TurnEndedS *float64
	CacheLifeS float64
}

// StayWithin is T: the cold start's cost as seconds of the window, the default only while nothing was measured. A measured 0 is a
// start that cost nothing, so T is 0.
func StayWithin(coldStartPct *float64, windowS float64) float64 {
	if coldStartPct == nil || windowS <= 0 {
		return DefaultStayWithinS
	}
	return *coldStartPct / 100 * windowS
}

// Decide is whether to move, and why. Never for an idle agent: a move is for work that is waiting. Waiting pays only when the resume
// finds the prompt cache warm and the reset comes sooner than a cold start would pay back (T, capped at the cache's life). An unknown
// reset is further than T.
func Decide(s Situation) (move bool, why string) {
	if !s.WorkWaiting {
		return false, "no work is waiting - an idle agent does not move"
	}
	if s.ResetInS == nil {
		return true, "work is waiting and its account's reset is unknown"
	}
	if s.TurnEndedS != nil && *s.TurnEndedS+*s.ResetInS > s.CacheLifeS {
		return true, "work is waiting and its prompt cache is cold by the reset - waiting saves nothing"
	}
	within := min(StayWithin(s.ColdStartPct, s.WindowS), s.CacheLifeS)
	if *s.ResetInS <= within {
		return false, fmt.Sprintf("its account resets within %dm - cheaper to wait", int(math.Floor(within/60)))
	}
	return true, "work is waiting and its account resets too late to wait for"
}
