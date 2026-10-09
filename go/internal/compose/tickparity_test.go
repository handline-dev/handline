package compose

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"os"
	osexec "os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/store"
)

// The Python daemon's usage steps of a tick (poll_usage, then observe_burn), run once on a copy of the home the Go tick runs on with
// -cutover, with the clock held at one second and the meter answering what the fake CLI answers (five hours 40%, seven days 10%, ending when the case says).
const pythonUsageSteps = `
import json, os, sys, time
sys.path.insert(0, os.getcwd())
T = int(sys.argv[1])
R5, R7 = (int(x) or None for x in sys.argv[2].split(","))
time.time = lambda: T
from handline import db
from handline.daemon import evaluate, usage_poll
from handline.team import accounts
from handline.usage import usage_api
from handline.vendors.base import Reading, Window
accounts.logged_in = lambda acct: True
usage_api.read = lambda acct, refresh=True: Reading(acct, (Window(300, 40, R5), Window(10080, 10, R7)), T)
con = db.connect()
usage_poll.poll_usage(con, lambda s: None)
evaluate.observe_burn(con, lambda s: None)
con.commit()
`

// usageTraces is what a tick leaves of the usage poll and the observations after it, in a form two homes can be compared in.
func usageTraces(t *testing.T, home string) map[string]any {
	t.Helper()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	out := map[string]any{
		"usage_samples": rowsOf(t, d, "SELECT * FROM usage_samples ORDER BY ts, account", "id"),
		"token_calls":   rowsOf(t, d, "SELECT * FROM token_calls ORDER BY account, session, req", "id"),
		// A NULL value is a cleared key; Python leaves the row, Go removes it, and nothing reads the difference.
		"meta": rowsOf(t, d, "SELECT name, value FROM meta WHERE value IS NOT NULL AND (name LIKE 'burn%' OR name LIKE 'weekratio%' OR name LIKE 'tokenoffsets:%' "+
			"OR name LIKE 'usage-fail:%' OR name LIKE 'reset-ping:%') ORDER BY name"),
		"cursors": rowsOf(t, d, "SELECT name, row_id FROM cursors WHERE name LIKE 'usage-%' ORDER BY name"),
	}
	status := map[string]any{}
	for _, a := range paths.ListAccounts(home) {
		b, err := os.ReadFile(paths.StatusFile(home, a))
		if err != nil {
			continue
		}
		var v any
		if err := json.Unmarshal(b, &v); err != nil {
			t.Fatalf("%s: status.json: %v", a, err)
		}
		status[a] = v
	}
	out["status.json"] = status
	return out
}

// rowsOf is the query's rows as maps, without the named columns.
func rowsOf(t *testing.T, d *sql.DB, query string, drop ...string) []map[string]any {
	t.Helper()
	rs, err := d.Query(query)
	if err != nil {
		t.Fatal(err)
	}
	defer rs.Close()
	cols, _ := rs.Columns()
	var rows []map[string]any
	for rs.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rs.Scan(ptrs...); err != nil {
			t.Fatal(err)
		}
		row := map[string]any{}
		for i, c := range cols {
			if b, ok := vals[i].([]byte); ok {
				vals[i] = string(b)
			}
			if str, ok := vals[i].(string); ok && (strings.HasPrefix(str, "{") || strings.HasPrefix(str, "[")) { // the same JSON, spaced as each side writes it
				var v any
				if json.Unmarshal([]byte(str), &v) == nil {
					vals[i] = v
				}
			}
			row[c] = vals[i]
		}
		for _, c := range drop {
			delete(row, c)
		}
		rows = append(rows, row)
	}
	return rows
}

func copyTree(t *testing.T, from, to string) {
	t.Helper()
	err := filepath.Walk(from, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(from, p)
		dst := filepath.Join(to, rel)
		if info.IsDir() {
			return os.MkdirAll(dst, 0o755)
		}
		if info.Mode()&os.ModeType != 0 {
			return nil
		}
		src, err := os.Open(p)
		if err != nil {
			return err
		}
		defer src.Close()
		w, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, info.Mode().Perm())
		if err != nil {
			return err
		}
		defer w.Close()
		if _, err := io.Copy(w, src); err != nil {
			return err
		}
		return os.Chtimes(dst, info.ModTime(), info.ModTime())
	})
	if err != nil {
		t.Fatal(err)
	}
}

// usageCase is a stored reading (5h and 7d: percent, reset offset from now) and what the meter answers (reset offsets, 0 for none).
type usageCase struct {
	name                       string
	stored                     [2]struct{ pct, reset int64 }
	want5                      float64 // the 5h figure both sides must end on, so the case cannot pass by changing nothing
	storedPing                 bool    // the ended 5h window has had its real turn, so neither side spends another
	answerReset5, answerReset7 int64
}

func TestTheGoTickAtTheCutoverLeavesTheUsageTracesThePythonTickLeaves(t *testing.T) {
	h := int64(3600)
	for _, c := range []usageCase{
		{name: "windows without a reset", stored: [2]struct{ pct, reset int64 }{{55, h}, {5, 2 * h}}},
		// 55% stored and 40% read inside the one open window: the figure is never lowered.
		{name: "a lower reading in the same open window", stored: [2]struct{ pct, reset int64 }{{55, h}, {12, 2 * h}}, answerReset5: h, answerReset7: 2 * h, want5: 55},
		// The stored 5h window ended 10 minutes ago; the reading names the next one.
		{name: "a reading after the window ended", stored: [2]struct{ pct, reset int64 }{{55, -600}, {12, 2 * h}}, storedPing: true, answerReset5: h, answerReset7: 2 * h, want5: 40},
	} {
		t.Run(c.name, func(t *testing.T) { compareUsageTicks(t, c) })
	}
}

func compareUsageTicks(t *testing.T, c usageCase) {
	py, err := osexec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is not installed")
	}
	root, _ := filepath.Abs("../../..")
	now := time.Now().Truncate(time.Second)
	p, _, _, _ := wired(t)
	home, d := p.home, p.Sink.DB
	// Two calls of one request, the later one with more output, and a call of another session: the log keeps the largest of a request.
	cfgDir := filepath.Join(home, "accounts", "a1", ".claude", "projects", "-w")
	os.MkdirAll(cfgDir, 0o755)
	call := func(session, req string, at time.Time, out int) string {
		b, _ := json.Marshal(map[string]any{"type": "assistant", "requestId": req, "sessionId": session, "timestamp": at.UTC().Format(time.RFC3339), "cwd": "/w",
			"message": map[string]any{"model": "claude-test", "usage": map[string]any{"input_tokens": 3, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 7, "output_tokens": out}}})
		return string(b)
	}
	lines := strings.Join([]string{call("s1", "r1", now.Add(-time.Hour), 4), call("s1", "r1", now.Add(-time.Hour), 9), call("s2", "r2", now.Add(-time.Minute), 2)}, "\n") + "\n"
	tx := filepath.Join(cfgDir, "s1.jsonl")
	os.WriteFile(tx, []byte(lines), 0o644)
	os.Chtimes(tx, now, now)
	// bob, a terminal agent the Go tick leaves alone, is mid-turn on a1, so the burn observation has a stretch to open.
	if err := dbpkg.SetAgentState(d, "bob", "RUNNING", 0, "test"); err != nil {
		t.Fatal(err)
	}
	if _, err := d.Exec("UPDATE agent_state SET account = 'a1', closed_at = NULL WHERE agent = 'bob'"); err != nil {
		t.Fatal(err)
	}
	if _, err := d.Exec("PRAGMA wal_checkpoint(TRUNCATE)"); err != nil {
		t.Fatal(err)
	}
	// a1 already has a reading, old enough to be asked again, whose windows the new reading replaces.
	old := now.Add(-3 * time.Hour).Unix()
	var stored []map[string]any
	for i, m := range []int{300, 10080} {
		stored = append(stored, map[string]any{"minutes": m, "used_percent": c.stored[i].pct, "resets_at": now.Unix() + c.stored[i].reset, "observed_at": old, "carried": false, "lost": false})
	}
	seed, _ := json.Marshal(map[string]any{"collected_at": old, "session_id": "keep-me", "windows": stored})
	os.WriteFile(paths.StatusFile(home, "a1"), seed, 0o644)
	at := func(off int64) int64 { // a reset offset as the clock's second, 0 for none
		if off == 0 {
			return 0
		}
		return now.Unix() + off
	}
	if c.storedPing { // an ended window earns a real turn once per reset, and neither side may spend it here
		if _, err := d.Exec("INSERT INTO meta(name, value, updated) VALUES('reset-ping:a1:5h', ?, 0)", fmt.Sprint(at(c.stored[0].reset))); err != nil {
			t.Fatal(err)
		}
	}
	resets := fmt.Sprintf("%d,%d", at(c.answerReset5), at(c.answerReset7))
	pyHome := t.TempDir()
	copyTree(t, home, pyHome)

	cmd := osexec.Command(py, "-I", "-c", pythonUsageSteps, fmt.Sprint(now.Unix()), resets)
	cmd.Dir = root
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "HANDLINE_") && !strings.HasPrefix(kv, "OCP_") { // a session's own home names would be read first
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, "OCP_HOME="+pyHome, "OCP_TEST_ROOT="+t.TempDir(), "PYTHONPATH="+root)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if out, err := cmd.Output(); err != nil {
		t.Fatalf("the Python steps: %v\n%s%s", err, out, stderr.String())
	}

	cfg := p.Run.cfg
	cfg.Now = func() time.Time { return now }
	if c.answerReset5 != 0 {
		cfg.Env = append(append([]string(nil), cfg.Env...), "FAKE_USAGE_RESETS="+resets)
	}
	tk := p.Tick(cfg, TickConfig{Cutover: true})
	if _, err := tk.Tick.Run(nil); err != nil {
		t.Fatal(err)
	}
	if _, err := d.Exec("PRAGMA wal_checkpoint(TRUNCATE)"); err != nil {
		t.Fatal(err)
	}

	goTr, pyTr := usageTraces(t, home), usageTraces(t, pyHome)
	for _, k := range []string{"usage_samples", "token_calls", "meta", "cursors"} {
		if len(pyTr[k].([]map[string]any)) == 0 {
			t.Fatalf("the Python steps left no %s: the comparison would hold of nothing", k)
		}
	}
	if c.want5 != 0 {
		ws := pyTr["status.json"].(map[string]any)["a1"].(map[string]any)["windows"].([]any)
		if got := ws[0].(map[string]any)["used_percent"]; got != c.want5 {
			t.Fatalf("the Python steps left the 5h window at %v, want %v", got, c.want5)
		}
	}
	for _, k := range []string{"usage_samples", "token_calls", "meta", "cursors", "status.json"} {
		if !reflect.DeepEqual(goTr[k], pyTr[k]) {
			g, _ := json.MarshalIndent(goTr[k], "", " ")
			y, _ := json.MarshalIndent(pyTr[k], "", " ")
			t.Errorf("%s differs\n go: %s\n py: %s", k, g, y)
		}
	}
}
