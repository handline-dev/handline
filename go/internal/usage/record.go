package usage

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/paths"
)

// recordSkewS is how far a window's reset may be in the past before the window counts as ended: the reset is the server's and the
// comparison is against a local clock, so the slack is for ordinary skew (usage_api.CLOCK_SKEW_S).
const recordSkewS = 60

// Record leaves a reading where every reader already looks (usage_api.record): the account's status file, merged into what is there,
// so a session's id, transcript pointer and context figures survive. Inside one window usage only rises, so a figure is never lowered
// while its own window is open; across a rollover it is replaced outright. It reports whether the file was written.
func Record(home string, r Reading) bool {
	return RecordFile(paths.StatusFile(home, r.Account), r)
}

// RecordFile is Record to a given file. The account's directory must exist: a reading for an account that is not there is not made one.
func RecordFile(path string, r Reading) bool {
	if st, err := os.Stat(filepath.Dir(path)); err != nil || !st.IsDir() {
		return false
	}
	prev := map[string]any{}
	if b, err := os.ReadFile(path); err == nil {
		var v any
		if decodeNumbers(b, &v) == nil {
			if m, ok := v.(map[string]any); ok {
				prev = m
			}
		}
	}
	snap := make(map[string]any, len(prev)+3)
	for k, v := range prev {
		snap[k] = v
	}
	old := map[int]map[string]any{}
	for _, e := range entries(prev) {
		if m, ok := isInt(e["minutes"]); ok {
			old[m] = e
		}
	}
	ws := append([]ReadingWindow(nil), r.Windows...)
	sort.SliceStable(ws, func(i, j int) bool { return ws[i].Minutes < ws[j].Minutes })
	out := make([]any, 0, len(ws))
	for _, w := range ws {
		var pct any
		if w.Pct != nil {
			value := *w.Pct
			before := old[w.Minutes]
			oldPct, hasPct := isInt(before["used_percent"])
			oldReset, hasReset := isInt(before["resets_at"])
			same := hasReset && int64(oldReset) == w.Reset && int64(oldReset) > r.At-recordSkewS
			if hasPct && same && oldPct > value {
				value = oldPct
			}
			pct = value
		}
		var reset any
		if w.Reset != 0 {
			reset = w.Reset
		}
		// Observed first-hand, now: not carried, and not lost.
		out = append(out, map[string]any{"minutes": w.Minutes, "used_percent": pct, "resets_at": reset, "observed_at": r.At,
			"carried": false, "lost": false})
	}
	snap["windows"] = out
	// Without collected_at the account fails closed as stale before its figures are read.
	snap["collected_at"] = r.At
	snap["rl_source"] = "api"

	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if enc.Encode(snap) != nil {
		return false
	}
	return atomicfile.Write(path, buf.String()) == nil
}
