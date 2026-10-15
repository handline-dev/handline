package usage

import (
	"bytes"
	"encoding/json"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/paths"
)

// The pull side of a session's status (usage/collect.py with vendors/claude_collect.py): what the session's own transcript says about its
// context, found under the account whose directory the session writes into, and left where the status readers already look.

// ctxSizes is each model's context window, by id prefix: the transcript does not carry it. An unknown model gets no figure, never a guess.
var ctxSizes = []struct {
	prefix string
	size   int64
}{{"claude-opus-5", 1_000_000}, {"claude-sonnet-5", 1_000_000}, {"claude-haiku-5-5", 1_000_000}, {"claude-haiku-4-5", 200_000}}

// ContextWindow is the model's context window, 0 when unknown.
func ContextWindow(model string) int64 {
	for _, c := range ctxSizes {
		if strings.HasPrefix(model, c.prefix) {
			return c.size
		}
	}
	return 0
}

const (
	pullTail    = 256 * 1024
	pullTailMax = 8 * 1024 * 1024
	synthetic   = "<synthetic>" // Claude Code's own messages (an interrupt notice) carry a usage of zeros
)

// usageKeys are the four counts a turn's context is made of.
var usageKeys = []string{"input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"}

func tailOf(path string, n int64) ([]byte, bool, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, false, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, false, err
	}
	off := max(0, st.Size()-n)
	if _, err := f.Seek(off, io.SeekStart); err != nil {
		return nil, false, err
	}
	b, err := io.ReadAll(f)
	return b, off == 0, err
}

func lines(b []byte) [][]byte { return bytes.Split(b, []byte("\n")) }

// lastTurn is the usage and model of the last real assistant turn, and whether the whole file was read: no turn in a file read whole has
// spent nothing; no turn in a tail that gave up knows nothing. A tail that finds nothing is doubled, up to a ceiling.
func lastTurn(path string) (map[string]int64, string, bool) {
	for size := int64(pullTail); ; size *= 2 {
		blob, whole, err := tailOf(path, size)
		if err != nil {
			return nil, "", false
		}
		ls := lines(blob)
		for i := len(ls) - 1; i >= 0; i-- {
			if !bytes.Contains(ls[i], []byte(`"usage"`)) {
				continue
			}
			var rec struct {
				Message *struct {
					Model string         `json:"model"`
					Usage map[string]any `json:"usage"`
				} `json:"message"`
			}
			if json.Unmarshal(ls[i], &rec) != nil || rec.Message == nil || rec.Message.Usage == nil {
				continue
			}
			slim, sum := map[string]int64{}, int64(0)
			for _, k := range usageKeys {
				if v, ok := rec.Message.Usage[k].(float64); ok && v == math.Trunc(v) {
					slim[k] = int64(v)
					sum += int64(v)
				}
			}
			if sum <= 0 || rec.Message.Model == synthetic {
				continue
			}
			return slim, rec.Message.Model, whole
		}
		if whole || size >= pullTailMax {
			return nil, "", whole
		}
	}
}

// costState is the session's own cost record, when it wrote one (not every version does).
func costState(path string) map[string]any {
	blob, _, err := tailOf(path, pullTail)
	if err != nil {
		return nil
	}
	ls := lines(blob)
	for i := len(ls) - 1; i >= 0; i-- {
		if !bytes.Contains(ls[i], []byte(`"cost-state"`)) {
			continue
		}
		var rec map[string]any
		if json.Unmarshal(ls[i], &rec) == nil && rec["type"] == "cost-state" {
			return rec
		}
	}
	return nil
}

var notAlnum = regexp.MustCompile(`[^A-Za-z0-9]`)

// located is the transcript a session writes, and what its path says for free.
type located struct {
	account, sessionID, path string
	at                       int64
}

// locate is the newest transcript for cwd across every account, or with sessionID that session's and no other: the session chose the
// directory it writes into, so its path names the account.
func locate(home, cwd, sessionID string) *located {
	var best *located
	for _, acct := range paths.ListAccounts(home) {
		dir := filepath.Join(paths.AccountDir(home, acct), ".claude", "projects", notAlnum.ReplaceAllString(cwd, "-"))
		files, _ := filepath.Glob(filepath.Join(dir, "*.jsonl"))
		for _, f := range files {
			stem := strings.TrimSuffix(filepath.Base(f), ".jsonl")
			if sessionID != "" && stem != sessionID {
				continue
			}
			st, err := os.Stat(f)
			if err != nil {
				continue
			}
			if at := st.ModTime().Unix(); best == nil || at > best.at {
				best = &located{account: acct, sessionID: stem, path: f, at: at}
			}
		}
	}
	return best
}

// PullSnapshot is one session's snapshot, in the shape the status readers expect; nil when no transcript is found.
func PullSnapshot(home, agent, cwd, sessionID string, now int64) map[string]any {
	found := locate(home, cwd, sessionID)
	if found == nil {
		return nil
	}
	used, model, whole := lastTurn(found.path)
	size := ContextWindow(model)
	var pct, sizeV, usedV, modelV any
	if used != nil {
		var sum int64
		for _, v := range used {
			sum += v
		}
		usedV = used
		if size > 0 && sum > 0 {
			pct = max(0, min(100, int(float64(sum)*100/float64(size)+0.5)))
		}
	} else if whole {
		pct = 0 // answered nothing yet: its context is its preamble
	}
	if size > 0 {
		sizeV = size
	}
	if model != "" {
		modelV = model
	}
	cost := costState(found.path)
	return map[string]any{
		"account": found.account, "agent": agent,
		// When the session last wrote, not when this read: the figure staleness is judged by.
		"collected_at": found.at, "pulled_at": now,
		"session_id": found.sessionID, "transcript_path": found.path, "model_id": modelV, "cwd": cwd,
		"ctx_used": usedV, "ctx_size": sizeV, "ctx_pct": pct,
		"cost_usd": cost["totalCostUSD"], "lines_added": cost["totalLinesAdded"], "lines_removed": cost["totalLinesRemoved"],
		"source": "pull",
	}
}

// CollectSnapshot pulls agent's snapshot and writes it to its status file; false when there was none to write.
func CollectSnapshot(home, agent, cwd, sessionID string) bool {
	snap := PullSnapshot(home, agent, cwd, sessionID, time.Now().Unix())
	if snap == nil {
		return false
	}
	p := paths.AgentStatusFile(home, agent)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return false
	}
	b, err := json.MarshalIndent(snap, "", "  ")
	if err != nil {
		return false
	}
	return atomicfile.Write(p, string(b)+"\n") == nil
}
