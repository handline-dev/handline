package rpc

import "github.com/handline/handline/go/internal/authority"

// Roles, lowest first: a call below the method's role is refused.
var roles = []string{"anonymous", "agent", "member", "founder"}

func roleIndex(r string) int {
	for i, x := range roles {
		if x == r {
			return i
		}
	}
	return -1
}

// Caller is who is on the other end, and only on positive proof: an agent when the peer process descends
// from a session `hl launch` recorded, the founder when hello carried the founder key, anyone else anonymous.
// A peer nothing can be traced for proves nothing.
type Caller struct {
	Role string
	Info map[string]any
}

// Human is the name a write is made under for the founder and any device.
const Human = "human"

func identify(a authority.Authority, local bool, pid int, key, founderName string) Caller {
	if local {
		if agent := a.AgentOf(pid); agent != "" { // wins over a key: a session that held the founder key would be a leak
			return Caller{"agent", map[string]any{"kind": "agent", "name": agent}}
		}
		if a.FounderKeyOK(key) {
			return Caller{"founder", map[string]any{"kind": "person", "name": founderName}}
		}
	}
	return Caller{"anonymous", map[string]any{"kind": "anonymous", "name": "anonymous"}}
}

// Actor is the name a write is made under: an agent's own, else human.
func (c Caller) Actor() string {
	if c.Role == "agent" {
		return c.Info["name"].(string)
	}
	return Human
}

func (c Caller) Allows(role string) bool { return roleIndex(c.Role) >= roleIndex(role) }

// ActorInfo is the spec's Actor for an event: a person is a user there.
func (c Caller) ActorInfo() map[string]any {
	out := map[string]any{}
	for k, v := range c.Info {
		out[k] = v
	}
	if out["kind"] == "person" {
		out["kind"] = "user"
	}
	return out
}
