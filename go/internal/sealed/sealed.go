// Package sealed keeps team.yaml and agent.yaml sealed: only the founder's own path can change what they say
// (handline/infra/sealed.py). Both files sit under the home, which every agent's sandbox can write, so their location is no
// lock. Each is instead sealed: the hash of the text the founder's session last wrote is kept in the protected store, and a
// reader refuses a file that no longer matches. Seals begin once: the first founder-side read on an installation from before
// them seals what is there and leaves a marker; after that a file with no seal is refused.
package sealed

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"regexp"
	"strings"
	"sync/atomic"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/yamlcompat"
)

const (
	NS       = "seal"
	Team     = "team"
	migrated = "migrated"
)

var unplain = regexp.MustCompile(`[^A-Za-z0-9._-]`)

// AgentKey is the seal key of an agent's agent.yaml.
func AgentKey(name string) string { return "agent-" + unplain.ReplaceAllString(name, "_") }

func digest(text string) string {
	h := sha256.Sum256([]byte(text))
	return hex.EncodeToString(h[:])
}

// Seals is the seals of one home.
type Seals struct {
	Home      string
	Store     *protected.Store
	observing atomic.Int32
}

// New is the seals kept for home.
func New(home string) *Seals { return &Seals{Home: home, Store: protected.New(home)} }

// RequireFounder is an error when the caller is an agent's session.
func (s *Seals) RequireFounder() error {
	if protected.InAgentSession() {
		return protected.ErrNotTheFounder
	}
	return nil
}

func (s *Seals) isMigrated() bool {
	_, ok := s.Store.Get(NS, migrated)
	return ok
}

// Seal records the digest of text under key, beginning the seals first when they have not begun.
func (s *Seals) Seal(key, text string) error {
	if !s.isMigrated() {
		if _, err := s.SealEverything(); err != nil {
			return err
		}
	}
	return s.Store.Put(NS, key, digest(text))
}

// Forget drops a seal.
func (s *Seals) Forget(key string) error { return s.Store.Delete(NS, key) }

// Observing runs f with reads that seal nothing, not even the first time: hl doctor.
func (s *Seals) Observing(f func()) {
	s.observing.Add(1)
	defer s.observing.Add(-1)
	f()
}

// Adopted is one file SealEverything took on: its label and whether the seal was new or changed.
type Adopted struct{ Label, Kind string }

// SealEverything seals team.yaml and every agent.yaml as they are now, and marks the seals as begun. The founder's act. It
// returns what this took on that was not already sealed as it is.
func (s *Seals) SealEverything() ([]Adopted, error) {
	var adopted []Adopted
	take := func(label, key, text string) error {
		d := digest(text)
		had, ok := s.Store.Get(NS, key)
		if !ok || strings.TrimSpace(had) != d {
			kind := "changed"
			if !ok {
				kind = "new"
			}
			adopted = append(adopted, Adopted{label, kind})
		}
		return s.Store.Put(NS, key, d)
	}
	if b, err := os.ReadFile(paths.TeamFile(s.Home)); err == nil {
		if err := take("team.yaml", Team, string(b)); err != nil {
			return adopted, err
		}
	}
	for _, name := range paths.ListAgents(s.Home) {
		b, err := os.ReadFile(paths.AgentDir(s.Home, name) + "/agent.yaml")
		if err != nil {
			return adopted, err
		}
		if err := take("agent "+name, AgentKey(name), string(b)); err != nil {
			return adopted, err
		}
	}
	return adopted, s.Store.Put(NS, migrated, "1")
}

// State is ok, changed (does not match its seal), unsealed (a file nothing sealed, after seals began) or legacy (seals have not begun).
func (s *Seals) State(key, text string) string {
	if want, ok := s.Store.Get(NS, key); ok {
		if strings.TrimSpace(want) == digest(text) {
			return "ok"
		}
		return "changed"
	}
	if s.isMigrated() {
		return "unsealed"
	}
	return "legacy"
}

// Mismatch is whether text is not what was sealed: changed, or unsealed.
func (s *Seals) Mismatch(key, text string) bool {
	st := s.State(key, text)
	return st == "changed" || st == "unsealed"
}

// Follow is a founder-side rewrite of a file by something other than a write path (a db migration): keep the seal only if it was holding before.
func (s *Seals) Follow(key, before, after string) {
	if !protected.InAgentSession() && s.State(key, before) == "ok" {
		_ = s.Seal(key, after)
	}
}

// Check is a ConfigError when text, read from where, is not what was sealed.
func (s *Seals) Check(key, text, where string) error {
	st := s.State(key, text)
	if st == "legacy" && !protected.InAgentSession() && s.observing.Load() == 0 {
		_, _ = s.SealEverything()
		st = s.State(key, text)
	}
	if st == "ok" || st == "legacy" {
		return nil
	}
	why := "was never sealed"
	if st == "changed" {
		why = "was changed outside the founder's path"
	}
	return &yamlcompat.ConfigError{Msg: fmt.Sprintf("%s %s and is not trusted. Roster and routing change only through an approved `hl hr` proposal or the founder's own session; if this file is the founder's, run `hl team seal`.", where, why)}
}
