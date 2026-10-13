// Package states is the states an agent is in, what wakes each, and the timing constants the daemon runs on (session/states.py, daemon/states.py).
package states

import (
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
)

const (
	Idle      = "IDLE"
	Queued    = "QUEUED"
	Running   = "RUNNING"
	Done      = "DONE"
	Handoff   = "HANDOFF"
	Degraded  = "DEGRADED"
	Parked    = "PARKED"
	Throttled = "THROTTLED"
	Stalled   = "STALLED"
)

// Evaluated is what the tick looks at (8.4). Anything but STALLED has a way back.
var Evaluated = map[string]bool{Running: true, Handoff: true, Parked: true, Queued: true, Throttled: true, Idle: true, Degraded: true}

// Waker says what wakes each state the daemon can write; a state without a way back would sit idle for ever.
var Waker = map[string]string{
	Running:   "the agent's own Stop hook ends its turn - the daemon never touches a RUNNING row",
	Idle:      "would_wake, the moment a request or an answer is waiting for it",
	Queued:    "would_wake dispatches it at once - it already has work",
	Handoff:   "the handoff's own step commits, or expire_handoffs() times it out into DEGRADED",
	Degraded:  "the daemon retries it itself after DegradedRetryS",
	Parked:    "wake_at - the usage window, the async-reply fallback, or the short move recheck",
	Throttled: "wake_at - the ask window rolls over (team.ASK_WINDOW_S)",
	Stalled:   "a person: w, behind a confirmation (unstall, folded into wake)",
}

const (
	// MovingMark starts the detail of a QUEUED or PARKED row that is the move machinery waiting, not a usage wall.
	MovingMark = "moving"
	// ByHandMark starts the detail of a park a person started by hand.
	ByHandMark = "by hand"
	// FoldKind is the event the tick writes when it closes an idle session fresh for its full context, and the cost-pilot switch for it.
	FoldKind = "idle_fold"
)

// ParkWakeable is park_wakeable: whether w does anything on this PARKED, THROTTLED or DEGRADED row, and why not.
func ParkWakeable(row *db.AgentState) (bool, string) {
	if strings.HasPrefix(row.Detail.String, MovingMark) {
		return false, fmt.Sprintf("%s is moving accounts - it retries on its own shortly", row.Agent)
	}
	if strings.HasPrefix(row.Detail.String, ByHandMark) {
		return true, ""
	}
	when := "?"
	if row.WakeAt.Valid && row.WakeAt.Int64 != 0 {
		when = time.Unix(row.WakeAt.Int64, 0).Format("15:04")
	}
	return false, fmt.Sprintf("%s clears on its own - starts %s when the window rolls over", row.Agent, when)
}

// The timing constants, in seconds.
const (
	LimitSettleS           = 30
	DegradedRetryS         = 600
	AssumedAccountRecheckS = 300
	LoginCheckCooldownS    = 300
	UsagePollS             = 120
	UsageMaxAgeS           = 900
	UsageRefreshCooldownS  = 900
	UsageFailBackoffCapS   = 1800
	MaxFailedAttempts      = 3
	MaxThrottleStreak      = 5
	IdleCloseS             = 600
	PromptCacheS           = 3600
	ResumeWarmS            = PromptCacheS - 300
	StuckRunningS          = 1800
	HandoffDeadlineS       = 90
	BackoffBaseS           = 60
	BackoffMaxS            = 3600
	MinHandoffIntervalS    = 120
)

// BackoffFor is backoff_for: seconds to wait before retrying after streak failed attempts.
func BackoffFor(streak int) time.Duration {
	if streak <= 0 {
		return 0
	}
	s := BackoffBaseS << (streak - 1)
	if streak > 20 || s > BackoffMaxS {
		s = BackoffMaxS
	}
	return time.Duration(s) * time.Second
}
