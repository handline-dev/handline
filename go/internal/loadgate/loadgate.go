// Package loadgate says whether the machine is too loaded to start more work (docs/story2-load-research.md, 2-1). It is level-triggered
// on the latest measure and has no model: over a limit a new start waits, and it goes once a later measure is under. Running agents are
// never stopped, and a machine with no agent mid-turn never holds a start, so work never stalls on the load alone.
package loadgate

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/sysres"
	"github.com/handline/handline/go/internal/usage"
)

// The limits' config.yaml keys and their defaults. The agent cap is off by default; the load and memory limits are on.
const (
	AgentCapKey       = "host_active_cap"
	LoadPctKey        = "load_per_core_pct"
	RAMFreePctKey     = "ram_free_min_pct"
	DefaultLoadPct    = 150
	DefaultRAMFreePct = 10
)

// Limits are the machine's limits; 0 turns one off.
type Limits struct {
	Agents     int // most agents mid-turn on the machine
	LoadPct    int // most 1-minute load per core, in percent
	RAMFreePct int // least free memory, in percent of all
}

// LimitsOf reads the limits from config, each at its default when it is not set to a whole number of at least 0.
func LimitsOf(cfg usage.Config) Limits {
	return Limits{Agents: setting(cfg, AgentCapKey, 0), LoadPct: setting(cfg, LoadPctKey, DefaultLoadPct),
		RAMFreePct: setting(cfg, RAMFreePctKey, DefaultRAMFreePct)}
}

func setting(cfg usage.Config, key string, def int) int {
	switch n := cfg[key].(type) {
	case int:
		if n >= 0 {
			return n
		}
	case int64:
		if n >= 0 {
			return int(n)
		}
	}
	return def
}

// HeldPrefix starts every reason Held gives, so a caller can tell a load hold from any other refusal (IsHeld).
const HeldPrefix = "Held: "

// IsHeld is whether why is a reason Held gave.
func IsHeld(why string) bool { return strings.HasPrefix(why, HeldPrefix) }

// Held is why a new start must wait, "" when it may go. active is the agents mid-turn on the machine; m is the latest measure, nil
// before the first, when only the agent cap is checked.
func Held(l Limits, active int, m *sysres.Machine) string {
	if active == 0 {
		return ""
	}
	if l.Agents > 0 && active >= l.Agents {
		return fmt.Sprintf(HeldPrefix+"%d of %d agents are working. It starts by itself when one finishes.", active, l.Agents)
	}
	if m == nil {
		return ""
	}
	if l.LoadPct > 0 && m.Cores > 0 {
		if pct := int(m.Load1 * 100 / float64(m.Cores)); pct > l.LoadPct {
			load, limit := perCore(pct, l.LoadPct)
			return HeldPrefix + "the Mac is busy (" + load + " per core, limit " + limit + "). It starts by itself when that drops."
		}
	}
	if l.RAMFreePct > 0 && m.RAMTotal > 0 {
		if pct := int(m.RAMFree * 100 / m.RAMTotal); pct < l.RAMFreePct {
			return fmt.Sprintf(HeldPrefix+"the Mac is low on memory (%d%% free, limit %d%%). It starts by itself when memory frees up.", pct,
				l.RAMFreePct)
		}
	}
	return ""
}

// perCore writes a load and its limit (percent of a core) with one decimal, or two when one would show them equal (1.51 over 1.50).
func perCore(load, limit int) (string, string) {
	a, b := fmt.Sprintf("%.1f", float64(load)/100), fmt.Sprintf("%.1f", float64(limit)/100)
	if a == b {
		a, b = fmt.Sprintf("%.2f", float64(load)/100), fmt.Sprintf("%.2f", float64(limit)/100)
	}
	return a, b
}

// PassFor is how long a force start lets an agent past the limits: long enough for the start it asked for, after which the agent counts
// as mid-turn like any other.
const PassFor = 2 * time.Minute

// Gate is Held over the machine as it stands. Limits, Active and Machine are read at each ask; Machine may be nil (no measure).
type Gate struct {
	Limits  func() Limits
	Active  func() int
	Machine func() *sysres.Machine
	Now     func() time.Time

	mu     sync.Mutex
	passed map[string]time.Time
	since  map[string]time.Time // when ForWork first found the agent's work held
}

func (g *Gate) now() time.Time {
	if g.Now != nil {
		return g.Now()
	}
	return time.Now()
}

// Held is why a new start for agent must wait, "" when it may go: a nil gate holds nothing.
func (g *Gate) Held(agent string) string {
	if g == nil {
		return ""
	}
	g.mu.Lock()
	until, ok := g.passed[agent]
	g.mu.Unlock()
	if ok && g.now().Before(until) {
		return ""
	}
	var m *sysres.Machine
	if g.Machine != nil {
		m = g.Machine()
	}
	return Held(g.Limits(), g.Active(), m)
}

// Ceiling is the longest the load holds an agent's waiting work: past it the work goes once, as a force start would.
const Ceiling = 10 * time.Minute

// ForWork is Held for an agent with work waiting, with Ceiling on it: the first ask held Ceiling ago lets the agent past once and calls
// onCeiling with why it had been held. Only the start and delivery paths ask it, so an agent with nothing waiting starts no clock. A nil
// gate holds nothing.
func (g *Gate) ForWork(agent string, onCeiling func(why string)) string {
	if g == nil {
		return ""
	}
	why := g.Held(agent)
	now := g.now()
	g.mu.Lock()
	if why == "" {
		delete(g.since, agent)
		g.mu.Unlock()
		return ""
	}
	if g.since == nil {
		g.since = map[string]time.Time{}
	}
	since, ok := g.since[agent]
	if !ok {
		g.since[agent] = now
		since = now
	}
	if now.Sub(since) < Ceiling {
		g.mu.Unlock()
		return why
	}
	delete(g.since, agent)
	g.pass(agent)
	g.mu.Unlock()
	if onCeiling != nil {
		onCeiling(why)
	}
	return ""
}

// Forget drops agent's hold clock: its caller found nothing waiting for it, so the next work it gets starts a clock of its own.
func (g *Gate) Forget(agent string) {
	if g == nil {
		return
	}
	g.mu.Lock()
	delete(g.since, agent)
	g.mu.Unlock()
}

// Pass lets agent's next start past the limits (a person's force start).
func (g *Gate) Pass(agent string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.pass(agent)
}

func (g *Gate) pass(agent string) {
	if g.passed == nil {
		g.passed = map[string]time.Time{}
	}
	for a, until := range g.passed {
		if !g.now().Before(until) {
			delete(g.passed, a)
		}
	}
	g.passed[agent] = g.now().Add(PassFor)
}
