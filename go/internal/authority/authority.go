// Package authority answers who a caller is, on proof only, from the founder's protected store
// (handline/infra/protected.py, sessiontoken.py). What it reads are the founder's decisions: which process
// is an agent's session and the key the founder's own client holds. No Claude credential is read or stored here.
package authority

import (
	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/sessiontoken"
)

const (
	sessionNS = sessiontoken.NS
	founderNS = sessiontoken.FounderNS
)

type Authority interface {
	// AgentOf is the agent whose recorded session process pid is, or descends from; "" for any other process.
	AgentOf(pid int) string
	// FounderKeyOK says whether key is the founder's.
	FounderKeyOK(key string) bool
}

// Store reads the protected store kept for one home; the proofs are sessiontoken's.
type Store struct {
	Root   string
	Lookup func(pid int) (peer.Proc, bool)
}

// Root is the protected store kept for home.
func Root(home string) string { return protected.RootFor(home) }

// New is the store for home.
func New(home string) *Store { return &Store{Root: Root(home), Lookup: peer.Lookup} }

func (s *Store) tokens() *sessiontoken.Tokens {
	return &sessiontoken.Tokens{Store: &protected.Store{Root: s.Root}, Lookup: s.Lookup}
}

func (s *Store) FounderKeyOK(key string) bool { return s.tokens().FounderKeyOK(key) }

func (s *Store) AgentOf(pid int) string { return s.tokens().AgentOf(pid) }

// SessionsReadable is nil when AgentOf can tell: the recorded sessions are read, or there are none.
func (s *Store) SessionsReadable() error { return s.tokens().SessionsReadable() }
