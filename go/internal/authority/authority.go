// Package authority answers who a caller is, on proof only, from the founder's protected store
// (handline/infra/protected.py, sessiontoken.py). What it reads are the founder's decisions: which process
// is an agent's session and the key the founder's own client holds. No Claude credential is read or stored here.
package authority

import (
	"crypto/subtle"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/peer"
)

const (
	sessionNS = "session-tokens"
	founderNS = "founder-keys"
	maxDepth  = 64
)

type Authority interface {
	// AgentOf is the agent whose recorded session process pid is, or descends from; "" for any other process.
	AgentOf(pid int) string
	// FounderKeyOK says whether key is the founder's.
	FounderKeyOK(key string) bool
}

// Store reads the protected store kept for one home.
type Store struct {
	Root   string
	Lookup func(pid int) (peer.Proc, bool)
}

var nonAlnum = regexp.MustCompile(`[^A-Za-z0-9]`)

// Root is the protected store kept for home: ~/.config/orcapipe/protected/<the resolved home with every other
// character a dash>.
func Root(home string) string {
	if real, err := filepath.EvalSymlinks(home); err == nil {
		home = real
	}
	abs, err := filepath.Abs(home)
	if err == nil {
		home = abs
	}
	return filepath.Join(paths.UserHome(), ".config", "orcapipe", "protected", nonAlnum.ReplaceAllString(home, "-"))
}

// New is the store for home.
func New(home string) *Store {
	return &Store{Root: Root(home), Lookup: peer.Lookup}
}

func (s *Store) get(ns, name string) string {
	b, err := os.ReadFile(filepath.Join(s.Root, ns, name))
	if err != nil {
		return "" // unreadable is what a session's sandbox says, and it grants nothing
	}
	return strings.TrimSpace(string(b))
}

func (s *Store) FounderKeyOK(key string) bool {
	kept := s.get(founderNS, "key")
	return kept != "" && key != "" && subtle.ConstantTimeCompare([]byte(kept), []byte(key)) == 1
}

func (s *Store) verify(agent string, pid int) bool {
	head, started, _ := strings.Cut(s.get(sessionNS, agent), " ")
	want := 0
	if head == "" || strings.Trim(head, "0123456789") != "" {
		return false
	}
	for _, c := range head {
		want = want*10 + int(c-'0')
	}
	for i := 0; i < maxDepth; i++ {
		p, ok := s.Lookup(pid)
		if !ok || pid <= 1 {
			return false
		}
		if pid == want {
			return p.Start == strings.TrimSpace(started) // a reused pid has another start time
		}
		pid = p.PPid
	}
	return false
}

func (s *Store) AgentOf(pid int) string {
	if pid <= 0 {
		return ""
	}
	entries, err := os.ReadDir(filepath.Join(s.Root, sessionNS))
	if err != nil {
		return ""
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	for _, n := range names {
		if s.verify(n, pid) {
			return n
		}
	}
	return ""
}
