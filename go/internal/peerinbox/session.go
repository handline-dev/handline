// Package peerinbox delivers a message to a running Claude Code terminal session through its peer inbox, a unix socket, without
// typing into the terminal. The socket gives no reply and drops a message silently (a body equal to the last within 30 s, more
// than a burst of 30, a refill of 0.5/s), so a send is only believed once the receiver's transcript shows it.
//
// The session's key authenticates one line on the socket. It is read into memory for that line and is in no struct, error or log.
package peerinbox

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// ProtocolVersion is the only peerProtocol this package speaks.
const ProtocolVersion = 1

// Why a session cannot be reached by its inbox; a caller falls back to something else for any of them.
var (
	ErrNoSession = errors.New("no live session record")
	ErrNoSocket  = errors.New("session has no peer inbox socket")
	ErrProtocol  = errors.New("session speaks a peer protocol this package does not")
	ErrNoKey     = errors.New("session has no key file")
)

// Session is what <config>/sessions/<pid>.json says about a running session. The key is not here; Key's file is.
type Session struct {
	PID       int
	SessionID string
	Cwd       string
	Version   string
	Socket    string
	// State, Detail, Tempo and Needs are how the session says whether it is busy, idle or blocked on a person.
	Status string
	State  string
	Detail string
	Tempo  string
	Needs  any
	Config string // the account config dir the record came from
	// UpdatedAt is the record's own clock, in ms.
	UpdatedAt int64

	keyPath string
}

// String is for logs: the pid and the state, not the key file's name, which carries a hash.
func (s Session) String() string {
	return fmt.Sprintf("session %d (%s) %s", s.PID, s.SessionID, s.Status)
}

type record struct {
	PID          int    `json:"pid"`
	SessionID    string `json:"sessionId"`
	Cwd          string `json:"cwd"`
	Version      string `json:"version"`
	PeerProtocol int    `json:"peerProtocol"`
	Socket       string `json:"messagingSocketPath"`
	Status       string `json:"status"`
	State        string `json:"state"`
	Detail       string `json:"detail"`
	Tempo        string `json:"tempo"`
	Needs        any    `json:"needs"`
	UpdatedAt    int64  `json:"updatedAt"`
}

// ByPID reads <config>/sessions/<pid>.json. It refuses a record with no socket, a peerProtocol other than ProtocolVersion, no
// version, or no key file.
func ByPID(config string, pid int) (Session, error) {
	dir := filepath.Join(config, "sessions")
	raw, err := os.ReadFile(filepath.Join(dir, strconv.Itoa(pid)+".json"))
	if err != nil {
		if os.IsNotExist(err) {
			return Session{}, ErrNoSession
		}
		return Session{}, err
	}
	var r record
	if err := json.Unmarshal(raw, &r); err != nil {
		return Session{}, fmt.Errorf("session record %d: %w", pid, err)
	}
	if r.Socket == "" {
		return Session{}, ErrNoSocket
	}
	if r.PeerProtocol != ProtocolVersion || r.Version == "" {
		return Session{}, ErrProtocol
	}
	keys, _ := filepath.Glob(filepath.Join(dir, strconv.Itoa(pid)+".*.key"))
	if len(keys) == 0 {
		return Session{}, ErrNoKey
	}
	return Session{
		PID: pid, SessionID: r.SessionID, Cwd: r.Cwd, Version: r.Version, Socket: r.Socket,
		Status: r.Status, State: r.State, Detail: r.Detail, Tempo: r.Tempo, Needs: r.Needs,
		Config: config, UpdatedAt: r.UpdatedAt, keyPath: keys[0],
	}, nil
}

// ByCwd is the session in cwd that was written last and that alive says still runs. A record that is not usable is passed over;
// if none is, the error is the last refusal seen (ErrNoSession when there was no record at all).
func ByCwd(config, cwd string, alive func(pid int) bool) (Session, error) {
	files, _ := filepath.Glob(filepath.Join(config, "sessions", "*.json"))
	best, err := Session{}, ErrNoSession
	for _, f := range files {
		pid, perr := strconv.Atoi(strings.TrimSuffix(filepath.Base(f), ".json"))
		if perr != nil || (alive != nil && !alive(pid)) {
			continue
		}
		s, serr := ByPID(config, pid)
		if serr != nil {
			if best.PID == 0 {
				err = serr
			}
			continue
		}
		if s.Cwd != cwd {
			continue
		}
		if best.PID == 0 || s.UpdatedAt > best.UpdatedAt {
			best, err = s, nil
		}
	}
	return best, err
}

// Blocked says the session waits on a person (a permission prompt, a usage choice): its record says waiting or blocked, or names what it
// needs. A message sent to it is only queued until the prompt is answered, and the answer is a keypress, not the inbox.
func (s Session) Blocked() bool {
	return s.Status == "waiting" || s.State == "waiting" || s.Tempo == "blocked" || (s.Needs != nil && s.Needs != "" && s.Needs != false)
}

// readKey returns the key, trimmed of its line end. The caller zeroes it.
func (s Session) readKey() ([]byte, error) {
	if s.keyPath == "" {
		return nil, ErrNoKey
	}
	k, err := os.ReadFile(s.keyPath)
	if err != nil {
		// os's error text names the path, which names a hash, not the key.
		return nil, ErrNoKey
	}
	n := len(k)
	for n > 0 && (k[n-1] == '\n' || k[n-1] == '\r') {
		n--
	}
	return k[:n], nil
}
