package lands

import (
	"sync"
	"time"
)

// GrantTTL is how long the session's own `hl land` command stays vouched for.
const GrantTTL = 60 * time.Second

// Grants is what the session's own `hl land` command vouched for, held for a minute and used once (handline/daemon/landauth.py).
// The session's PreToolUse hook records a grant when it sees the exact command; the daemon takes it when the matching verb arrives.
// The sha is the text typed in the command, which is also what `hl land` sends: a grant binds one agent, one verb and that text.
type Grants struct {
	Now func() time.Time

	mu     sync.Mutex
	grants map[grantKey]time.Time
}

type grantKey struct{ agent, verb, sha string }

func (g *Grants) now() time.Time {
	if g.Now != nil {
		return g.Now()
	}
	return time.Now()
}

// Grant vouches for agent's verb on exactly sha until GrantTTL from now. Grants that ran out are dropped as it goes.
func (g *Grants) Grant(agent, verb, sha string) {
	now := g.now()
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.grants == nil {
		g.grants = map[grantKey]time.Time{}
	}
	for k, exp := range g.grants {
		if !exp.After(now) {
			delete(g.grants, k)
		}
	}
	g.grants[grantKey{agent, verb, sha}] = now.Add(GrantTTL)
}

// Take is whether a live grant for exactly this agent, verb and sha existed; it is spent either way.
func (g *Grants) Take(agent, verb, sha string) bool {
	now := g.now()
	g.mu.Lock()
	exp, ok := g.grants[grantKey{agent, verb, sha}]
	delete(g.grants, grantKey{agent, verb, sha})
	g.mu.Unlock()
	return ok && exp.After(now)
}
