// Package devices is who may use the network listener: one-time pairing codes, device tokens kept as hashes,
// and the connections a device has open, which revoking it closes at once (contract/protocol.md, "Network").
package devices

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

const (
	DefaultTTL = 300 * time.Second
	MaxTTL     = 600 * time.Second
	// wrongPerCode is how many wrong tries a live code survives; wrongPerHost wrong tries within hostWindow lock a
	// host out of device.redeem for hostLock.
	wrongPerCode = 5
	wrongPerHost = 5
	hostWindow   = 10 * time.Minute
	hostLock     = 15 * time.Minute
	codeAlphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
)

// Device is a paired device as devices.list shows it.
type Device struct {
	ID       string
	Name     string
	Created  int64
	LastSeen *int64
}

// Store keeps the paired devices; a token is only ever given to it as a hash.
type Store interface {
	Add(d Device, tokenHash string) error
	ByTokenHash(hash string) (Device, bool, error)
	Delete(id string) (bool, error)
	List() ([]Device, error)
	Touch(id string, ts int64) error
}

type code struct {
	expires time.Time
	wrong   int
}

// Manager is the pairing state of one daemon. Pairing codes live in memory only: a restart voids them.
type Manager struct {
	store Store
	now   func() time.Time

	mu         sync.Mutex
	addr       string
	print      func() string
	codes      map[string]*code // by the hash of the code
	wrongs     map[string][]time.Time
	lockedTill map[string]time.Time
	conns      map[string]map[int]func() // by device id: what closes each open connection
	next       int
}

func New(store Store) *Manager {
	return &Manager{store: store, now: time.Now, codes: map[string]*code{}, wrongs: map[string][]time.Time{},
		lockedTill: map[string]time.Time{}, conns: map[string]map[int]func(){}}
}

// Listening records where the listener is and the fingerprint of its certificate, which a pairing uri carries.
func (m *Manager) Listening(addr string, fingerprint func() string) {
	m.mu.Lock()
	m.addr, m.print = addr, fingerprint
	m.mu.Unlock()
}

func hashOf(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

func random(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

func newCode() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	var sb strings.Builder
	for i, x := range b {
		if i > 0 && i%4 == 0 {
			sb.WriteByte('-')
		}
		sb.WriteByte(codeAlphabet[int(x)%len(codeAlphabet)])
	}
	return sb.String()
}

// Pair makes a one-time code that lives ttl.
func (m *Manager) Pair(ttl time.Duration) (c string, expires time.Time, fingerprint, uri string) {
	c = newCode()
	m.mu.Lock()
	defer m.mu.Unlock()
	expires = m.now().Add(ttl)
	m.codes[hashOf(c)] = &code{expires: expires}
	var fp string
	if m.print != nil {
		fp = m.print()
	}
	q := url.Values{"host": {m.addr}, "fp": {fp}, "code": {c}}
	return c, expires, fp, "handline://pair?" + q.Encode()
}

func pairingRefused() *rpc.Error {
	return rpc.Refused("that pairing code is not valid", "pairing_code", "")
}

// Redeem trades a code for a device token, once. remote is the caller's host: wrong tries are counted per host.
func (m *Manager) Redeem(c, name, remote string) (token string, d Device, err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	now := m.now()
	if until, locked := m.lockedTill[remote]; locked {
		if now.Before(until) {
			return "", Device{}, rpc.Refused("too many wrong pairing codes from this address", "attempt_limit", "")
		}
		delete(m.lockedTill, remote)
	}
	h := hashOf(strings.ToUpper(strings.TrimSpace(c)))
	live, ok := m.codes[h]
	if ok && !now.Before(live.expires) {
		delete(m.codes, h)
		ok = false
	}
	if !ok {
		m.wrongTry(remote, now)
		return "", Device{}, pairingRefused()
	}
	token = "hld_" + random(32)
	d = Device{ID: "dev_" + random(6), Name: name, Created: now.Unix()}
	if err := m.store.Add(d, hashOf(token)); err != nil {
		return "", Device{}, err
	}
	delete(m.codes, h)
	return token, d, nil
}

// wrongTry counts a wrong code against every live code and the host. Called with m.mu held.
func (m *Manager) wrongTry(remote string, now time.Time) {
	for h, c := range m.codes {
		if c.wrong++; c.wrong >= wrongPerCode || !now.Before(c.expires) {
			delete(m.codes, h)
		}
	}
	for h, ts := range m.wrongs {
		if now.Sub(ts[len(ts)-1]) >= hostWindow {
			delete(m.wrongs, h) // a host that has been quiet a whole window starts over
		}
	}
	var recent []time.Time
	for _, t := range m.wrongs[remote] {
		if now.Sub(t) < hostWindow {
			recent = append(recent, t)
		}
	}
	recent = append(recent, now)
	m.wrongs[remote] = recent
	if len(recent) >= wrongPerHost {
		m.lockedTill[remote] = now.Add(hostLock)
		delete(m.wrongs, remote)
	}
}

// Revoke cancels a device and closes its connections. false when it is not paired.
func (m *Manager) Revoke(id string) (bool, error) {
	m.mu.Lock()
	found, err := m.store.Delete(id)
	var closers []func()
	for _, f := range m.conns[id] {
		closers = append(closers, f)
	}
	delete(m.conns, id)
	m.mu.Unlock()
	for _, f := range closers {
		f()
	}
	return found, err
}

// List is the paired devices, each with whether it has a connection open.
func (m *Manager) List() ([]map[string]any, error) {
	ds, err := m.store.List()
	if err != nil {
		return nil, err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]map[string]any, 0, len(ds))
	for _, d := range ds {
		var seen any
		if d.LastSeen != nil {
			seen = time.Unix(*d.LastSeen, 0).UTC().Format("2006-01-02T15:04:05Z")
		}
		out = append(out, map[string]any{"id": d.ID, "name": d.Name, "role": "member",
			"created": time.Unix(d.Created, 0).UTC().Format("2006-01-02T15:04:05Z"), "last_seen": seen, "connected": len(m.conns[d.ID]) > 0})
	}
	return out, nil
}

// Know, Login and Live are rpc.NetGate.

func (m *Manager) Know(token string) bool {
	_, ok, err := m.store.ByTokenHash(hashOf(token))
	return ok && err == nil
}

func (m *Manager) Login(token string, closeConn func()) (id, name string, release func(), ok bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	d, found, err := m.store.ByTokenHash(hashOf(token))
	if !found || err != nil {
		return "", "", nil, false
	}
	m.next++
	n := m.next
	if m.conns[d.ID] == nil {
		m.conns[d.ID] = map[int]func(){}
	}
	if closeConn == nil {
		closeConn = func() {}
	}
	m.conns[d.ID][n] = closeConn
	m.store.Touch(d.ID, m.now().Unix())
	return d.ID, d.Name, func() {
		m.mu.Lock()
		defer m.mu.Unlock()
		delete(m.conns[d.ID], n)
		if len(m.conns[d.ID]) == 0 {
			delete(m.conns, d.ID)
		}
		m.store.Touch(d.ID, m.now().Unix())
	}, true
}

func (m *Manager) Live(id string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.conns[id]) > 0
}

var _ rpc.NetGate = (*Manager)(nil)
