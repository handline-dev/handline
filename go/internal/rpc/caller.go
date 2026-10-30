package rpc

import (
	"cmp"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/authority"
	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/sessiontoken"
)

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

// Writer is whose request_ids a write is kept under, as Python's Caller.writer: the actor, except a paired device, which has its own.
func (c Caller) Writer() string {
	if c.Info["kind"] == "device" {
		return "device:" + c.Info["device"].(string)
	}
	return c.Actor()
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

// logRefusal logs a founder's method refused to a caller that is not an agent (agents are refused by design), with what its hello said and
// the processes above it, so a refused founder client names its cause.
func (c *Conn) logRefusal(method, role string) {
	if role != "founder" || c.Caller.Role == "agent" || c.srv.Log == nil {
		return
	}
	chain, _ := processChain(c.Pid)
	c.srv.Log("  refused %s: needs %s, caller is %s; hello from %q with %s; pid %d (%s)", method, role, c.Caller.Role, c.helloClient,
		cmp.Or(c.helloKey, "no hello"), c.Pid, chain)
}

// processChain is pid's program and up to 8 programs above it ("hl:12 > zsh:11"), and its controlling terminal. Empty for pid 0, a peer
// the kernel did not name.
func processChain(pid int) (chain, terminal string) {
	if pid <= 0 {
		return "", ""
	}
	parts := []string{fmt.Sprintf("%s:%d", filepath.Base(peer.Exe(pid)), pid)}
	for i, a := range (&sessiontoken.Tokens{Lookup: peer.Lookup}).Chain(pid) {
		if i == 8 {
			break
		}
		parts = append(parts, fmt.Sprintf("%s:%d", a.Program, a.PID))
	}
	return strings.Join(parts, " > "), peer.Terminal(pid)
}

// noteFounderKey keeps an event for each hello the founder key was taken on: the process that presented it, the programs above it and its
// controlling terminal, so every use can be traced afterwards. A row that cannot be written is logged and refuses nothing.
func (s *Server) noteFounderKey(pid int, client string) {
	if s.DB == nil || s.NoEvents {
		return
	}
	chain, terminal := processChain(pid)
	payload := PyJSON(map[string]any{"pid": pid, "chain": chain, "terminal": terminal, "client": client}, false, false)
	if _, err := s.DB.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(?, ?, 'founder_key', ?)", time.Now().Unix(), Human, payload); err != nil && s.Log != nil {
		s.Log("  founder key: the record of pid %d was not written: %v", pid, err)
	}
}
