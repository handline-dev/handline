// Package protected is the protected store: what only the founder may say, kept where no agent can write it
// (handline/infra/protected.py). It lives outside the handline home, under ~/.config/orcapipe/protected, which an agent's
// sandbox does not list as writable; reading is open. It holds decisions, never secrets: no credential belongs here.
// Put and Delete also refuse when $OCP_AGENT names an agent, so where the sandbox is off the answer is still no from here:
// a convenience, not the lock; the lock is the location.
package protected

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/paths"
)

var (
	plain    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)
	nonAlnum = regexp.MustCompile(`[^A-Za-z0-9]`)
)

// ErrNotTheFounder is what a write from an agent's session gets.
var ErrNotTheFounder = errors.New("this is the founder's to set, from a terminal or the screen, not an agent's")

// Human is the founder's own name as a caller: not an agent.
const Human = "human"

// Store is the protected store kept for one home.
type Store struct{ Root string }

// RootFor is the store kept for home: ~/.config/orcapipe/protected/<the resolved home with every other character a dash>.
func RootFor(home string) string {
	if real, err := filepath.EvalSymlinks(home); err == nil {
		home = real
	}
	if abs, err := filepath.Abs(home); err == nil {
		home = abs
	}
	return filepath.Join(paths.UserHome(), ".config", "orcapipe", "protected", nonAlnum.ReplaceAllString(home, "-"))
}

// New is the store for home.
func New(home string) *Store { return &Store{Root: RootFor(home)} }

func (s *Store) entry(namespace, name string) (string, error) {
	for _, part := range []string{namespace, name} {
		if !plain.MatchString(part) {
			return "", fmt.Errorf("not a plain name: %q", part)
		}
	}
	return filepath.Join(s.Root, namespace, name), nil
}

// Get is the entry; ok is false when there is none, or when the store cannot be read at all, which is what an agent's
// sandbox says. For a decision or a grant that is the answer that grants nothing.
func (s *Store) Get(namespace, name string) (string, bool) {
	p, err := s.entry(namespace, name)
	if err != nil {
		return "", false
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return "", false
	}
	return string(b), true
}

// WrittenAt is when the entry was last written; ok is false when there is none or it cannot be read.
func (s *Store) WrittenAt(namespace, name string) (float64, bool) {
	p, err := s.entry(namespace, name)
	if err != nil {
		return 0, false
	}
	st, err := os.Stat(p)
	if err != nil {
		return 0, false
	}
	return float64(st.ModTime().UnixNano()) / 1e9, true
}

// IsOff is whether the founder recorded off. A store that cannot be read or holds no entry is not off: a gate that reads it
// stays on, so a sandbox can never switch one off by hiding the store.
func (s *Store) IsOff(namespace, name string) bool {
	v, ok := s.Get(namespace, name)
	return ok && v == "off"
}

// InAgentSession is whether $OCP_AGENT names an agent: anyone but the founder's own human.
func InAgentSession() bool {
	v, _ := paths.Getenv("AGENT")
	return v != "" && v != Human
}

func founderOnly() error {
	if InAgentSession() {
		return ErrNotTheFounder
	}
	return nil
}

// Files is every entry kept for the store, relative to it, for a backup.
func (s *Store) Files() []string {
	var out []string
	filepath.WalkDir(s.Root, func(p string, d fs.DirEntry, err error) error {
		if err == nil && !d.IsDir() && !strings.HasPrefix(d.Name(), ".put-") {
			if rel, err := filepath.Rel(s.Root, p); err == nil {
				out = append(out, rel)
			}
		}
		return nil
	})
	sort.Strings(out)
	return out
}

// Ensure makes the namespace's folder, private (0700, with the store's root) when this creates it.
func (s *Store) Ensure(namespace string) error {
	if err := founderOnly(); err != nil {
		return err
	}
	folder := filepath.Join(s.Root, namespace)
	if _, err := os.Stat(folder); err == nil {
		return nil
	}
	if err := os.MkdirAll(folder, 0o755); err != nil {
		return err
	}
	for _, d := range []string{s.Root, folder} {
		if err := os.Chmod(d, 0o700); err != nil {
			return err
		}
	}
	return nil
}

// Put records value.
func (s *Store) Put(namespace, name, value string) error {
	if err := founderOnly(); err != nil {
		return err
	}
	p, err := s.entry(namespace, name)
	if err != nil {
		return err
	}
	if err := s.Ensure(namespace); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(p), ".put-")
	if err != nil {
		return err
	}
	tmp := f.Name()
	_, werr := f.WriteString(value)
	cerr := f.Close()
	if werr != nil || cerr != nil {
		os.Remove(tmp)
		return errors.Join(werr, cerr)
	}
	if err := os.Rename(tmp, p); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

// Delete removes the entry; none is no error.
func (s *Store) Delete(namespace, name string) error {
	if err := founderOnly(); err != nil {
		return err
	}
	p, err := s.entry(namespace, name)
	if err != nil {
		return err
	}
	if err := os.Remove(p); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}

// Exposure is what makes this store no lock at all, for hl doctor: it sits inside a folder the agents write, or something
// other than its owner can write to it.
func (s *Store) Exposure(home string) []string {
	var out []string
	here, err := filepath.EvalSymlinks(s.Root)
	if err != nil {
		here = s.Root
	}
	ocpHome, err := filepath.EvalSymlinks(home)
	if err != nil {
		ocpHome = home
	}
	if here == ocpHome || strings.HasPrefix(here, ocpHome+string(filepath.Separator)) {
		out = append(out, fmt.Sprintf("%s is inside %s, which every agent can write", here, ocpHome))
	}
	node := here
	for {
		if _, err := os.Stat(node); err == nil || filepath.Dir(node) == node {
			break
		}
		node = filepath.Dir(node)
	}
	if st, err := os.Stat(node); err == nil && st.Mode().Perm()&0o022 != 0 {
		out = append(out, fmt.Sprintf("%s can be written by its group or others", node))
	}
	return out
}
