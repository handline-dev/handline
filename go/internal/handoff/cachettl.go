package handoff

import (
	"encoding/json"
	"io"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

// A session's prompt-cache clock, read from its transcript (handline/session/cachettl.py).
//
// Claude Code writes the cache with a one-hour or a five-minute life, and every response says which: usage.cache_creation splits
// what it wrote into ephemeral_1h_input_tokens and ephemeral_5m_input_tokens. A subscription is on the hour; once an account draws
// on usage credits the main conversation drops to five minutes, so reading the TTL off the response follows that by itself. A
// response that only read the cache wrote nothing and says nothing, so the TTL is the nearest earlier response that did. The clock
// itself, though, is the last response: every read restarts the life.
//
// Only Claude's transcript is read here. For a session whose transcript is not Claude's (or is unreadable) there is no clock, and
// the callers keep what they did before there was one.
const (
	LongTTLS  = 3600
	ShortTTLS = 300
	// lookBack is how many responses back to look for one that wrote the cache, when the last ones only read it.
	lookBack = 60
)

// chunks is the tail of a transcript read for the last responses: a turn's tool results can be large, so the read grows.
var chunks = []int64{256 * 1024, 2 * 1024 * 1024, 16 * 1024 * 1024}

// Clock is the last response's time and that cache's life. TTL is 0 when no response says.
type Clock struct {
	At  float64 // when the last response of the main conversation was written (epoch seconds)
	TTL int
}

// Left is the seconds until the cache lapses (negative once it has); ok is false when the TTL is not known.
func (c Clock) Left(now float64) (float64, bool) {
	if c.TTL == 0 {
		return 0, false
	}
	return c.At + float64(c.TTL) - now, true
}

// Margin is what a resume needs under the TTL to reach its first request before the cache is gone: five minutes of an hour,
// scaled down for a short life.
func Margin(ttl int) int { return min(300, ttl/12) }

func isInt(v any) (int64, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	i, err := strconv.ParseInt(n.String(), 10, 64)
	return i, err == nil
}

func ttlOf(usage map[string]any) int {
	made, _ := usage["cache_creation"].(map[string]any)
	if made == nil {
		return 0
	}
	if n, ok := isInt(made["ephemeral_1h_input_tokens"]); ok && n > 0 {
		return LongTTLS
	}
	if n, ok := isInt(made["ephemeral_5m_input_tokens"]); ok && n > 0 {
		return ShortTTLS
	}
	return 0
}

func when(stamp any) (float64, bool) {
	s := "None"
	if stamp != nil {
		s = toStr(stamp)
	}
	s = strings.Replace(s, "Z", "+00:00", -1)
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05", "2006-01-02 15:04:05Z07:00", "2006-01-02 15:04:05", "2006-01-02"} {
		var t time.Time
		var err error
		if strings.Contains(layout, "Z07") || layout == time.RFC3339Nano {
			t, err = time.Parse(layout, s)
		} else {
			t, err = time.ParseInLocation(layout, s, time.Local) // a naive time is the local one, as Python reads it
		}
		if err == nil {
			return float64(t.UnixNano()) / 1e9, true
		}
	}
	return 0, false
}

func toStr(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case json.Number:
		return x.String()
	}
	b, _ := json.Marshal(v)
	return string(b)
}

func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case json.Number:
		f, _ := x.Float64()
		return f != 0
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

func tailLines(path string, size, want int64) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	if _, err := f.Seek(max(0, size-want), io.SeekStart); err != nil {
		return nil, err
	}
	data, err := io.ReadAll(f)
	if err != nil {
		return nil, err
	}
	lines := strings.Split(strings.ToValidUTF8(string(data), "�"), "\n")
	if size > want {
		return lines[1:], nil // the first line of a cut read is partial
	}
	return lines, nil
}

// scan is the clock (nil when there is no response): the last response, and the TTL of it or of the nearest earlier one that wrote
// the cache. whole is false when the lines ran out before either was found.
func scan(lines []string) (clock *Clock, whole bool) {
	var at float64
	found := false
	seen := 0
	for i := len(lines) - 1; i >= 0; i-- {
		line := lines[i]
		if !strings.Contains(line, `"usage"`) {
			continue
		}
		dec := json.NewDecoder(strings.NewReader(line))
		dec.UseNumber()
		var d map[string]any
		if err := dec.Decode(&d); err != nil || dec.More() {
			continue
		}
		msg, _ := d["message"].(map[string]any)
		usage, _ := msg["usage"].(map[string]any)
		if d["type"] != "assistant" || truthy(d["isSidechain"]) || usage == nil {
			continue
		}
		w, ok := when(d["timestamp"])
		if !ok {
			continue
		}
		if !found {
			at, found = w, true
		}
		ttl := ttlOf(usage)
		seen++
		if ttl != 0 {
			return &Clock{at, ttl}, true
		}
		if seen >= lookBack {
			return &Clock{at, 0}, true
		}
	}
	if found {
		return &Clock{at, 0}, false
	}
	return nil, false
}

type memoKey struct {
	mtime time.Time
	size  int64
}

type memoed struct {
	memoKey
	clock *Clock
}

var (
	memoMu sync.Mutex
	memo   = map[string]memoed{}
)

// ReadClock is the clock of the session whose transcript is at path, nil when there is none to read. Answered from memory while the
// file is unchanged, so asking every tick costs a stat.
func ReadClock(path string) *Clock {
	if path == "" {
		return nil
	}
	st, err := os.Stat(path)
	if err != nil {
		return nil
	}
	key := memoKey{st.ModTime(), st.Size()}
	memoMu.Lock()
	hit, ok := memo[path]
	memoMu.Unlock()
	if ok && hit.memoKey == key {
		return hit.clock
	}
	var clock *Clock
	for _, want := range chunks {
		lines, err := tailLines(path, st.Size(), want)
		if err != nil {
			return nil
		}
		var whole bool
		clock, whole = scan(lines)
		if whole || want >= st.Size() {
			break
		}
	}
	memoMu.Lock()
	if len(memo) > 256 {
		memo = map[string]memoed{}
	}
	memo[path] = memoed{key, clock}
	memoMu.Unlock()
	return clock
}
