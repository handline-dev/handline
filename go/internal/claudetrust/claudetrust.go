// Package claudetrust marks a folder trusted in a claude account's global config, so a session started there opens without the
// folder-trust dialog. The way it writes is Orca's (github.com/stablyai/orca, src/main/claude/claude-folder-trust-file.ts at
// dff65d55a, MIT License, Copyright (c) 2026 Lovecast Inc.): never create the file, never break claude's own lock, never rewrite a
// file that does not parse, and replace it by a rename from beside it. Unlike Orca it edits the text in place, so every byte outside
// the one entry it sets is the file's own.
package claudetrust

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Outcome is what Grant did.
type Outcome string

const (
	Granted    Outcome = "granted"
	Unchanged  Outcome = "unchanged"  // already trusted
	Missing    Outcome = "missing"    // no config yet: claude writes its own on first start
	Locked     Outcome = "locked"     // claude held its lock, or kept writing under us; claude asks
	Unreadable Outcome = "unreadable" // not a file, not JSON, or projects is not an object
)

const trustKey = "hasTrustDialogAccepted"

// lockWaits are the pauses between tries at claude's lock (Orca's: 4 retries, factor 2, 50 to 250 ms).
var lockWaits = []time.Duration{50 * time.Millisecond, 100 * time.Millisecond, 200 * time.Millisecond, 250 * time.Millisecond}

// rewrites is how many times a write is redone when the file changed between our read and our rename.
const rewrites = 3

var (
	mu      sync.Mutex
	byFile  = map[string]*sync.Mutex{}
	sleep   = time.Sleep
	preSwap func() // tests: runs between the last read and the rename
)

// ConfigFile is the global config claude reads under configDir: a legacy .config.json wins, as in claude itself.
func ConfigFile(configDir string) string {
	if legacy := filepath.Join(configDir, ".config.json"); fileExists(legacy) {
		return legacy
	}
	return filepath.Join(configDir, ".claude.json")
}

// Keys are the spellings claude may look folder up by: the absolute path and, when it differs, the one with links resolved.
func Keys(folder string) []string {
	abs, err := filepath.Abs(folder)
	if err != nil {
		return nil
	}
	keys := []string{abs}
	if real, err := filepath.EvalSymlinks(abs); err == nil && real != abs {
		keys = append(keys, real)
	}
	return keys
}

// Grant sets projects[key].hasTrustDialogAccepted = true for every key in configFile, unless one of them is trusted already.
func Grant(configFile string, keys []string) (Outcome, error) {
	m := fileMutex(configFile)
	m.Lock()
	defer m.Unlock()
	target, text, out := read(configFile)
	if out != "" {
		return out, nil
	}
	// Most launches find the folder trusted: claude's lock is taken only when there is something to write.
	if _, changed, err := apply(text, keys); err != nil {
		return Unreadable, nil
	} else if !changed {
		return Unchanged, nil
	}
	release, ok := takeLock(configFile + ".lock")
	if !ok {
		return Locked, nil
	}
	defer release()
	for range rewrites {
		now, cur, out := read(configFile)
		if out != "" {
			return out, nil
		}
		if now != target {
			return Unreadable, nil // the link was pointed elsewhere since: claude asks
		}
		next, changed, err := apply(cur, keys)
		if err != nil {
			return Unreadable, nil
		}
		if !changed {
			return Unchanged, nil
		}
		swapped, err := replace(target, cur, next)
		if err != nil {
			return Unreadable, err
		}
		if swapped {
			return Granted, nil
		}
	}
	return Locked, nil
}

func fileMutex(path string) *sync.Mutex {
	mu.Lock()
	defer mu.Unlock()
	if byFile[path] == nil {
		byFile[path] = &sync.Mutex{}
	}
	return byFile[path]
}

func fileExists(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.Mode().IsRegular()
}

// read is the file behind configFile (a link resolved, so the rename keeps the link) and its text, or why it cannot be rewritten.
func read(configFile string) (string, []byte, Outcome) {
	target, err := filepath.EvalSymlinks(configFile)
	if errors.Is(err, os.ErrNotExist) {
		return "", nil, Missing
	}
	if err != nil || !fileExists(target) {
		return "", nil, Unreadable
	}
	text, err := os.ReadFile(target)
	if err != nil {
		return "", nil, Unreadable
	}
	return target, text, ""
}

// takeLock is claude's own: proper-lockfile's directory at <file>.lock. A lock held is never broken; claude clears a stale one itself.
func takeLock(path string) (func(), bool) {
	for i := 0; ; i++ {
		err := os.Mkdir(path, 0o755)
		if err == nil {
			return func() { os.Remove(path) }, true
		}
		if !errors.Is(err, os.ErrExist) || i == len(lockWaits) {
			return nil, false
		}
		sleep(lockWaits[i])
	}
}

// replace renames next over target, with target's mode, only if target still holds was: claude writes without its lock when it cannot
// take it, and such a write landing after our read must not be lost. swapped is false when it changed.
func replace(target string, was, next []byte) (swapped bool, err error) {
	st, err := os.Stat(target)
	if err != nil {
		return false, err
	}
	tmp, err := os.CreateTemp(filepath.Dir(target), filepath.Base(target)+".handline-trust-*.tmp")
	if err != nil {
		return false, err
	}
	defer os.Remove(tmp.Name()) // a no-op after the rename
	_, werr := tmp.Write(next)
	cerr := tmp.Close()
	if err := errors.Join(werr, cerr, os.Chmod(tmp.Name(), st.Mode().Perm())); err != nil {
		return false, err
	}
	if preSwap != nil {
		preSwap()
	}
	if cur, err := os.ReadFile(target); err != nil || !bytes.Equal(cur, was) {
		return false, err
	}
	return true, os.Rename(tmp.Name(), target)
}

// apply is text with every key trusted, changed false when one already was. Each edit is a splice into the text.
func apply(text []byte, keys []string) ([]byte, bool, error) {
	if !json.Valid(text) {
		return nil, false, errors.New("not JSON")
	}
	for _, k := range keys {
		trusted, err := isTrusted(text, k)
		if err != nil || trusted {
			return nil, false, err
		}
	}
	for _, k := range keys {
		var err error
		if text, err = trust(text, k); err != nil {
			return nil, false, err
		}
	}
	return text, true, nil
}

func projects(text []byte) (top object, p *member, err error) {
	if top, err = parseObject(text, skipWS(text, 0)); err != nil {
		return
	}
	p = top.get(text, "projects")
	if p != nil && text[p.valStart] != '{' {
		err = errors.New("projects is not an object")
	}
	return
}

func isTrusted(text []byte, key string) (bool, error) {
	_, p, err := projects(text)
	if err != nil || p == nil {
		return false, err
	}
	ps, err := parseObject(text, p.valStart)
	if err != nil {
		return false, err
	}
	e := ps.get(text, key)
	if e == nil || text[e.valStart] != '{' {
		return false, nil
	}
	entry, err := parseObject(text, e.valStart)
	if err != nil {
		return false, err
	}
	t := entry.get(text, trustKey)
	return t != nil && string(text[t.valStart:t.valEnd]) == "true", nil
}

func trust(text []byte, key string) ([]byte, error) {
	top, p, err := projects(text)
	if err != nil {
		return nil, err
	}
	fresh := `{"` + trustKey + `": true}`
	if p == nil {
		return top.insert(text, "projects", "{"+quote(key)+": "+fresh+"}"), nil
	}
	ps, err := parseObject(text, p.valStart)
	if err != nil {
		return nil, err
	}
	e := ps.get(text, key)
	if e == nil {
		return ps.insert(text, key, fresh), nil
	}
	if text[e.valStart] != '{' {
		return splice(text, e.valStart, e.valEnd, fresh), nil
	}
	entry, err := parseObject(text, e.valStart)
	if err != nil {
		return nil, err
	}
	if t := entry.get(text, trustKey); t != nil {
		return splice(text, t.valStart, t.valEnd, "true"), nil
	}
	return entry.insert(text, trustKey, "true"), nil
}

// object is a JSON object's place in the text: start at its '{', end at its '}'.
type object struct {
	start, end int
	members    []member
}

// member is one key and value; lead is where the whitespace before its key begins.
type member struct {
	key                              string
	lead, keyStart, valStart, valEnd int
}

// get is the member under key: the last one, as JSON.parse keeps it.
func (o object) get(text []byte, key string) *member {
	for i := len(o.members) - 1; i >= 0; i-- {
		if o.members[i].key == key {
			return &o.members[i]
		}
	}
	return nil
}

// insert adds key: val as the last member, indented as the member before it is.
func (o object) insert(text []byte, key, val string) []byte {
	if len(o.members) == 0 {
		return splice(text, o.start+1, o.start+1, quote(key)+": "+val)
	}
	last := o.members[len(o.members)-1]
	return splice(text, last.valEnd, last.valEnd, ","+string(text[last.lead:last.keyStart])+quote(key)+": "+val)
}

func parseObject(text []byte, i int) (object, error) {
	if i >= len(text) || text[i] != '{' {
		return object{}, fmt.Errorf("no object at %d", i)
	}
	o := object{start: i}
	lead := i + 1
	i = skipWS(text, lead)
	if i < len(text) && text[i] == '}' {
		o.end = i
		return o, nil
	}
	for {
		var m member
		m.lead, m.keyStart = lead, i
		end, err := decodeAt(text, i, &m.key)
		if err != nil {
			return object{}, err
		}
		if i = skipWS(text, end); i >= len(text) || text[i] != ':' {
			return object{}, fmt.Errorf("no ':' at %d", i)
		}
		m.valStart = skipWS(text, i+1)
		var raw json.RawMessage
		if m.valEnd, err = decodeAt(text, m.valStart, &raw); err != nil {
			return object{}, err
		}
		o.members = append(o.members, m)
		switch i = skipWS(text, m.valEnd); {
		case i < len(text) && text[i] == ',':
			lead = i + 1
			i = skipWS(text, lead)
		case i < len(text) && text[i] == '}':
			o.end = i
			return o, nil
		default:
			return object{}, fmt.Errorf("no ',' or '}' at %d", i)
		}
	}
}

// decodeAt reads one JSON value starting exactly at i into v, and is where it ends.
func decodeAt(text []byte, i int, v any) (int, error) {
	d := json.NewDecoder(bytes.NewReader(text[i:]))
	if err := d.Decode(v); err != nil {
		return 0, err
	}
	return i + int(d.InputOffset()), nil
}

func skipWS(text []byte, i int) int {
	for i < len(text) && (text[i] == ' ' || text[i] == '\t' || text[i] == '\n' || text[i] == '\r') {
		i++
	}
	return i
}

func splice(text []byte, from, to int, s string) []byte {
	out := make([]byte, 0, len(text)+len(s))
	return append(append(append(out, text[:from]...), s...), text[to:]...)
}

func quote(s string) string {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	enc.Encode(s)
	return string(bytes.TrimRight(b.Bytes(), "\n"))
}
