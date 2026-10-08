package usage

import (
	"bytes"
	"encoding/json"
	"io"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

// What each API call spent, per account, kept beside the percentages (handline/usage/tokenlog.py): one token_calls row per call, read
// from the conversation logs under the account's own .claude/projects - never anything else under the account directory. Nothing in
// the picking rules reads these rows.

const (
	// TokenRetentionDays: kept longer than the samples they are fitted against.
	TokenRetentionDays = 60
	// BackfillDays: a transcript untouched for longer than this is not scanned at all.
	BackfillDays = 14
	// BytesPerTick of transcript are read per account per tick, so the first run catches up a slice at a time.
	BytesPerTick = 4 * 1024 * 1024
	// MaxRecordBytes bounds the overspend on one record that does not fit the tick's budget; a longer line is left unread.
	MaxRecordBytes = 64 * 1024 * 1024

	offsetsMeta = "tokenoffsets:"
)

// Call is one transcript line that carries a call's usage.
type Call struct {
	Session, Req string
	TS           int64
	Model, Cwd   string
	Side         int
	Tokens       Tokens
}

// callTS is tokenlog._ts: an ISO-8601 stamp with an offset, in unix seconds; one without an offset is not a time we can place.
func callTS(stamp string) (int64, bool) {
	t, err := time.Parse(time.RFC3339Nano, stamp)
	if err != nil {
		return 0, false
	}
	return t.Unix(), true
}

// tokenCount is int(v or 0): absent or null is 0, a number truncates, anything else is not a count.
func tokenCount(v any) (int64, bool) {
	if v == nil {
		return 0, true
	}
	f, ok := num(v)
	if !ok || math.IsNaN(f) || math.IsInf(f, 0) {
		return 0, false
	}
	return int64(f), true
}

// ParseCall is tokenlog._call: the call a transcript line carries, ok false for any other line.
func ParseCall(line []byte) (Call, bool) {
	if !bytes.Contains(line, []byte(`"usage"`)) {
		return Call{}, false
	}
	var v any
	if decodeNumbers(line, &v) != nil {
		return Call{}, false
	}
	o, _ := v.(map[string]any)
	req := requestID(o["requestId"])
	if o == nil || o["type"] != "assistant" || req == "" {
		return Call{}, false
	}
	m, _ := o["message"].(map[string]any)
	u, isMap := m["usage"].(map[string]any)
	ts, tsOK := callTS(strOf(o["timestamp"]))
	model := strOf(m["model"])
	// A synthetic message (an error the client wrote) carries no usage of a call.
	if !isMap || !tsOK || model == "" || strings.HasPrefix(model, "<") {
		return Call{}, false
	}
	c := Call{Session: strOf(o["sessionId"]), Req: req, TS: ts, Model: model, Cwd: strOf(o["cwd"])}
	if truthy(o["isSidechain"]) {
		c.Side = 1
	}
	details, _ := u["output_tokens_details"].(map[string]any)
	for _, f := range []struct {
		dst *int64
		src any
	}{{&c.Tokens.Inp, u["input_tokens"]}, {&c.Tokens.CC, u["cache_creation_input_tokens"]}, {&c.Tokens.CR, u["cache_read_input_tokens"]},
		{&c.Tokens.Out, u["output_tokens"]}, {&c.Tokens.Thinking, details["thinking_tokens"]}} {
		n, ok := tokenCount(f.src)
		if !ok {
			return Call{}, false
		}
		*f.dst = n
	}
	return c, true
}

func strOf(v any) string {
	s, _ := v.(string)
	return s
}

// requestID is str(requestId) for a truthy one.
func requestID(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case json.Number:
		if f, err := x.Float64(); err == nil && f != 0 {
			return x.String()
		}
	}
	return ""
}

// saveCalls files the calls. One request is written as several lines while it streams, the later ones with more output: the row keeps
// the largest of each.
func saveCalls(q db.Q, acct string, calls []Call) error {
	for _, c := range calls {
		if _, err := q.Exec("INSERT INTO token_calls(account, session, req, ts, model, cwd, side, inp, cc, cr, out, thinking) "+
			"VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account, session, req) DO UPDATE SET "+
			"out=MAX(out, excluded.out), thinking=MAX(thinking, excluded.thinking), "+
			"inp=MAX(inp, excluded.inp), cc=MAX(cc, excluded.cc), cr=MAX(cr, excluded.cr)",
			acct, c.Session, c.Req, c.TS, c.Model, c.Cwd, c.Side,
			c.Tokens.Inp, c.Tokens.CC, c.Tokens.CR, c.Tokens.Out, c.Tokens.Thinking); err != nil {
			return err
		}
	}
	return nil
}

type transcript struct {
	key, path string
	mtime     int64
	done      int64
}

// IngestTokens is tokenlog.ingest: read what the account's transcripts gained since the last call, up to budget bytes, and record it.
// It answers the lines that parsed as a call, which is more than the rows written when one streamed over several. A file is read from
// where it stopped, and only up to its last whole line. Only regular files are read: a link is not followed out of the projects tree.
func (e *Env) IngestTokens(q db.Q, acct string, budget int) (int, error) {
	if team.AccountVendor(e.Home, acct) != team.DefaultVendor {
		return 0, nil
	}
	dir, err := team.AccountConfigDir(e.Home, acct)
	if err != nil {
		return 0, nil
	}
	root := filepath.Join(dir, "projects")
	if st, err := os.Stat(root); err != nil || !st.IsDir() {
		return 0, nil
	}
	raw, _, err := db.Meta(q, offsetsMeta+acct)
	if err != nil {
		return 0, err
	}
	var kept map[string]any
	if raw == "" || !decodeObject(raw, &kept) || kept == nil {
		kept = map[string]any{}
	}
	fresh := int64(e.now()) - BackfillDays*86400
	var todo []transcript
	_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil || !d.Type().IsRegular() || !strings.HasSuffix(p, ".jsonl") {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		key, _ := filepath.Rel(root, p)
		var done int64
		if f, ok := num(kept[key]); ok {
			done = int64(f)
		}
		_, known := kept[key]
		if info.Size() > done && (known || info.ModTime().Unix() >= fresh) {
			todo = append(todo, transcript{key, p, info.ModTime().UnixNano(), done})
		}
		return nil
	})
	sort.Slice(todo, func(i, j int) bool {
		if todo[i].mtime != todo[j].mtime {
			return todo[i].mtime > todo[j].mtime
		}
		return todo[i].key > todo[j].key
	})
	n := 0
	for _, t := range todo {
		if budget <= 0 {
			break
		}
		chunk, end := readWhole(t.path, t.done, budget)
		if end == 0 {
			continue
		}
		budget -= end
		var calls []Call
		for _, line := range bytes.Split(chunk[:end], []byte("\n")) {
			if c, ok := ParseCall(line); ok {
				calls = append(calls, c)
			}
		}
		if len(calls) > 0 {
			if err := saveCalls(q, acct, calls); err != nil {
				return n, err
			}
			n += len(calls)
		}
		kept[t.key] = t.done + int64(end)
	}
	b, err := json.Marshal(kept)
	if err != nil {
		return n, err
	}
	return n, db.SetMeta(q, offsetsMeta+acct, string(b))
}

// readWhole reads up to budget bytes of the file from off and answers them with the length of the whole lines among them. A record
// that does not fit the budget is read whole rather than stall the file forever, up to MaxRecordBytes.
func readWhole(path string, off int64, budget int) ([]byte, int) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0
	}
	defer f.Close()
	if _, err := f.Seek(off, io.SeekStart); err != nil {
		return nil, 0
	}
	chunk, _ := io.ReadAll(io.LimitReader(f, int64(budget)))
	end := bytes.LastIndexByte(chunk, '\n') + 1
	if end == 0 && len(chunk) >= budget {
		rest := bytes.Buffer{}
		one := make([]byte, 1)
		for rest.Len() < MaxRecordBytes {
			if k, _ := f.Read(one); k == 0 {
				return chunk, 0 // the file ends inside a record: not whole yet
			}
			rest.WriteByte(one[0])
			if one[0] == '\n' {
				chunk = append(chunk, rest.Bytes()...)
				return chunk, len(chunk)
			}
		}
	}
	return chunk, end
}

// PruneTokens is tokenlog.prune: delete calls older than the retention window and report how many.
func (e *Env) PruneTokens(q db.Q) (int64, error) {
	return exec(q, "DELETE FROM token_calls WHERE ts < ?", int64(e.now())-TokenRetentionDays*86400)
}

// TokenTotals is tokenlog.totals: the tokens of each class the account spent in (lo, hi].
func TokenTotals(q db.Q, account string, lo, hi int64) (Tokens, error) {
	var t Tokens
	err := q.QueryRow("SELECT COALESCE(SUM(inp),0), COALESCE(SUM(cc),0), COALESCE(SUM(cr),0), COALESCE(SUM(out),0), "+
		"COALESCE(SUM(thinking),0) FROM token_calls WHERE account = ? AND ts > ? AND ts <= ?", account, lo, hi).
		Scan(&t.Inp, &t.CC, &t.CR, &t.Out, &t.Thinking)
	return t, err
}
