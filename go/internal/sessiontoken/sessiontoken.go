// Package sessiontoken is the proof that a request comes from an agent's own session (handline/infra/sessiontoken.py).
// There is no secret. hl launch records its own process id and start time for the agent it becomes in the protected store,
// which no agent session can write; the process it execs keeps that id, so every process the session starts descends from
// it. The daemon asks the kernel who is on the other end of a socket and accepts a verb only when that process descends from
// the recorded one. A name in the request proves nothing.
package sessiontoken

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/protected"
)

const (
	NS        = "session-tokens" // the store folder keeps its name; renaming it would orphan the records
	KeyNS     = "hook-keys"
	FounderNS = "founder-keys" // the daemon's key for the founder's own callers; hidden from sessions like KeyNS
	maxDepth  = 64
)

// Tokens reads and writes the records of one store. Lookup is the kernel's answer for a pid; tests replace it.
type Tokens struct {
	Store  *protected.Store
	Lookup func(pid int) (peer.Proc, bool)
}

// New is the tokens kept for home.
func New(home string) *Tokens { return &Tokens{Store: protected.New(home), Lookup: peer.Lookup} }

// Record remembers pid (this process when 0) as one of agent's sessions, beside those recorded before that are still running: two
// launches of one agent can race, and the one that dies must not take the proof from the one that lives. False when the store cannot be
// written, which is what an agent's session gets.
func (t *Tokens) Record(agent string, pid int) bool {
	if pid == 0 {
		pid = os.Getpid()
	}
	info, ok := t.Lookup(pid)
	if !ok {
		return false
	}
	lines := []string{fmt.Sprintf("%d %s", pid, info.Start)}
	for _, s := range t.sessions(agent) {
		if s.pid != pid && t.running(s) {
			lines = append(lines, fmt.Sprintf("%d %s", s.pid, s.start))
		}
	}
	return t.Store.Put(NS, agent, strings.Join(lines, "\n")) == nil
}

type session struct {
	pid   int
	start string
}

// sessions is what is recorded for agent, a "pid start" line each.
func (t *Tokens) sessions(agent string) []session {
	kept, _ := t.Store.Get(NS, agent)
	var out []session
	for _, line := range strings.Split(kept, "\n") {
		head, started, _ := strings.Cut(strings.TrimSpace(line), " ")
		if pid, err := strconv.Atoi(head); err == nil && head == strconv.Itoa(pid) && pid > 0 {
			out = append(out, session{pid, started})
		}
	}
	return out
}

func (t *Tokens) running(s session) bool {
	p, ok := t.Lookup(s.pid)
	return ok && p.Start == s.start // a reused pid has another start time
}

// Verify is whether process pid is, or descends from, a session recorded for agent.
func (t *Tokens) Verify(agent string, pid int) bool {
	if agent == "" || pid <= 0 {
		return false
	}
	recorded := t.sessions(agent)
	if len(recorded) == 0 {
		return false
	}
	for i := 0; i < maxDepth; i++ {
		p, ok := t.Lookup(pid)
		if !ok || pid <= 1 {
			return false
		}
		for _, s := range recorded {
			if pid == s.pid && p.Start == s.start {
				return true
			}
		}
		pid = p.PPid
	}
	return false
}

// AgentOf is the agent whose recorded session process pid is, or descends from; "" for any other process.
func (t *Tokens) AgentOf(pid int) string {
	if pid <= 0 {
		return ""
	}
	entries, err := os.ReadDir(filepath.Join(t.Store.Root, NS))
	if err != nil {
		return ""
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	for _, n := range names {
		if t.Verify(n, pid) {
			return n
		}
	}
	return ""
}

// SessionsReadable is nil when the recorded sessions can be told: the directory is read, or does not exist (no agent has
// a session). Any other failure means AgentOf cannot say who a process is, and its "" is not an answer.
func (t *Tokens) SessionsReadable() error {
	_, err := os.ReadDir(filepath.Join(t.Store.Root, NS))
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}

// IsClaude is whether a program path is Claude Code: the claude command, or its versioned binary in the folder the
// installer keeps them in. A copy run through node shows up as node and is not told.
func IsClaude(path string) bool {
	if path == "" {
		return false
	}
	return filepath.Base(path) == "claude" || strings.Contains(path, "/claude/versions/") ||
		strings.Contains(path, "/claude-code/") || strings.Contains(path, "/@anthropic-ai/claude")
}

// ClaudeAncestors is how many Claude Code processes sit above pid (this process when 0) in the process tree.
func (t *Tokens) ClaudeAncestors(pid int) int {
	if pid == 0 {
		pid = os.Getpid()
	}
	count := 0
	for i := 0; i < maxDepth; i++ {
		info, ok := t.Lookup(pid)
		if !ok || info.PPid <= 1 {
			break
		}
		pid = info.PPid
		if IsClaude(peer.Exe(pid)) {
			count++
		}
	}
	return count
}

// Nested is whether pid runs under a Claude Code that itself runs under another.
func (t *Tokens) Nested(pid int) bool { return t.ClaudeAncestors(pid) >= 2 }

// Ancestor is a process above another: its pid and the base name of its program.
type Ancestor struct {
	PID     int
	Program string
}

// Chain is each process above pid (this process when 0), for a record of who issued a command.
func (t *Tokens) Chain(pid int) []Ancestor {
	if pid == 0 {
		pid = os.Getpid()
	}
	var out []Ancestor
	for i := 0; i < maxDepth; i++ {
		info, ok := t.Lookup(pid)
		if !ok || info.PPid <= 1 {
			break
		}
		pid = info.PPid
		prog := "?"
		if exe := peer.Exe(pid); exe != "" {
			prog = filepath.Base(exe)
		}
		out = append(out, Ancestor{pid, prog})
	}
	return out
}

// KeyFolder is where the hooks' keys are kept.
func (t *Tokens) KeyFolder() string { return filepath.Join(t.Store.Root, KeyNS) }

// HiddenFolders are the key folders a session must not read.
func (t *Tokens) HiddenFolders() []string {
	return []string{t.KeyFolder(), filepath.Join(t.Store.Root, FounderNS)}
}

// EnsureKeyFolders makes the folders exist before a launch hides them; quietly nothing when the store is unwritable.
func (t *Tokens) EnsureKeyFolders() {
	_ = t.Store.Ensure(KeyNS)
	_ = t.Store.Ensure(FounderNS)
}

func newKey() string {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		panic(err) // no randomness is no key: fail closed
	}
	return base64.RawURLEncoding.EncodeToString(b)
}

// MintKey makes a new key for agent's hook, kept where the session's sandbox cannot read it. False when the store cannot be written.
func (t *Tokens) MintKey(agent string) bool { return t.Store.Put(KeyNS, agent, newKey()) == nil }

// KeyHeld is the key, for the hook, which the harness runs outside the sandbox; "" inside it.
func (t *Tokens) KeyHeld(agent string) string {
	v, _ := t.Store.Get(KeyNS, agent)
	return strings.TrimSpace(v)
}

func equal(kept, given string) bool {
	return kept != "" && given != "" && subtle.ConstantTimeCompare([]byte(kept), []byte(given)) == 1
}

// KeyOK is whether key is agent's hook key.
func (t *Tokens) KeyOK(agent, key string) bool {
	return agent != "" && equal(t.KeyHeld(agent), key)
}

// EnsureFounderKey makes the founder key when there is none. False when the store cannot be written.
func (t *Tokens) EnsureFounderKey() bool {
	if t.FounderKeyHeld() != "" {
		return true
	}
	return t.Store.Put(FounderNS, "key", newKey()) == nil
}

// FounderKeyHeld is the founder key, for the founder's own client; "" inside a session's sandbox.
func (t *Tokens) FounderKeyHeld() string {
	v, _ := t.Store.Get(FounderNS, "key")
	return strings.TrimSpace(v)
}

// FounderKeyOK is whether key is the founder's.
func (t *Tokens) FounderKeyOK(key string) bool { return equal(t.FounderKeyHeld(), key) }
