package sessionrpc

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

type auth struct{ agent string }

func (a auth) AgentOf(int) string         { return a.agent }
func (a auth) FounderKeyOK(k string) bool { return k == "key" }

type controls struct {
	calls []string
	ok    bool
	said  string
}

func (c *controls) rec(name string, args ...any) (bool, string, error) {
	parts := []string{name}
	for _, a := range args {
		parts = append(parts, fmt.Sprint(a))
	}
	c.calls = append(c.calls, strings.Join(parts, " "))
	return c.ok, c.said, nil
}
func (c *controls) Start(cl Call, a, repo, base string) (bool, string, error) {
	return c.rec("start", a, repo, base, cl.Person)
}
func (c *controls) Wake(_ Call, a string) (bool, string, error)          { return c.rec("wake", a) }
func (c *controls) Close(_ Call, a string, f bool) (bool, string, error) { return c.rec("close", a, f) }
func (c *controls) Restart(_ Call, a string, f, g bool) (bool, string, error) {
	return c.rec("restart", a, f, g)
}
func (c *controls) Park(_ Call, a string) (bool, string, error) { return c.rec("park", a) }

// procs is the structured sessions: ann has one.
type procs struct {
	live        map[string]bool
	interrupted []string
	killed      []string
	err         error
}

func (p *procs) Structured(a string) bool { return p.live[a] }
func (p *procs) Interrupt(a string) error {
	p.interrupted = append(p.interrupted, a)
	return p.err
}
func (p *procs) Kill(a string) error { p.killed = append(p.killed, a); return p.err }

type rig struct {
	e       *Env
	ctl     *controls
	pr      *procs
	closed  []string
	present bool
	t       *testing.T
}

func newRig(t *testing.T) *rig {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HANDLINE_HOME", home)
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	r := &rig{t: t, ctl: &controls{ok: true, said: "done"}, pr: &procs{live: map[string]bool{"ann": true}}}
	r.e = &Env{DB: d, Home: home, Controls: r.ctl, Procs: r.pr, Now: func() time.Time { return time.Unix(1_000_000, 0) },
		CloseTerminal:  func(h string) (bool, string) { r.closed = append(r.closed, h); return true, "" },
		SessionPresent: func(string) (bool, error) { return r.present, nil }}
	dir := filepath.Join(paths.AgentsDir(home), "ann")
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte("name: ann\n"), 0o644)
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated, terminal, worktree) VALUES('ann', 'IDLE', 0, '%1', '/w')"); err != nil {
		t.Fatal(err)
	}
	return r
}

type conn struct {
	name string
	c    net.Conn
	r    *bufio.Reader
	id   int
	t    *testing.T
}

// dial is one connection to a server with the methods registered, as the founder, or as ann's own session.
func (r *rig) dial(agent bool) *conn {
	srv := rpc.NewServer(auth{agent: map[bool]string{true: "ann"}[agent]}, "sha", 1, "local", func(string, ...any) {})
	srv.DB = r.e.DB
	Register(srv, r.e)
	cl, sv := net.Pipe()
	go func() { srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close() }()
	r.t.Cleanup(func() { cl.Close() })
	c := &conn{c: cl, r: bufio.NewReader(cl), t: r.t}
	hello := result(r.t, c.call("hello", map[string]any{"client": "t/1", "protocol": 1, "key": "key"}))
	c.name, _ = hello["caller"].(map[string]any)["name"].(string)
	return c
}

func (c *conn) call(method string, params map[string]any) map[string]any {
	c.t.Helper()
	c.id++
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": c.id, "method": method, "params": params})
	c.c.SetDeadline(time.Now().Add(5 * time.Second))
	c.c.Write(append(b, '\n'))
	line, err := c.r.ReadBytes('\n')
	if err != nil {
		c.t.Fatalf("%s: no reply: %v", method, err)
	}
	var m map[string]any
	json.Unmarshal(line, &m)
	return m
}

func result(t *testing.T, m map[string]any) map[string]any {
	t.Helper()
	r, ok := m["result"].(map[string]any)
	if !ok {
		t.Fatalf("want a result, got %v", m)
	}
	return r
}

// refused is the gate of a refusal, "" for any other reply.
func refused(m map[string]any) string {
	e, _ := m["error"].(map[string]any)
	d, _ := e["data"].(map[string]any)
	g, _ := d["gate"].(string)
	return g
}

func badField(m map[string]any) string {
	e, _ := m["error"].(map[string]any)
	d, _ := e["data"].(map[string]any)
	f, _ := d["field"].(string)
	return f
}

func rid(n string) string { return "r-" + n }

// ---- session methods

func TestStartPassesTheRepoAndBaseAndAPersonIsNotHeldBack(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	if f := badField(c.call("session.start", map[string]any{"agent": "ann", "account": "a1", "request_id": rid("1")})); f != "account" {
		t.Fatalf("an account is refused, got field %q", f)
	}
	if f := badField(c.call("session.start", map[string]any{"agent": "ann", "repo": 5, "request_id": rid("2")})); f != "repo" {
		t.Fatalf("field %q", f)
	}
	got := result(t, c.call("session.start", map[string]any{"agent": "ann", "repo": "/r", "request_id": rid("3")}))
	if got["accepted"] != true || got["said"] != "done" || r.ctl.calls[0] != "start ann /r main true" {
		t.Fatalf("%v %v", got, r.ctl.calls)
	}
	again := result(t, c.call("session.start", map[string]any{"agent": "ann", "repo": "/r", "request_id": rid("3")}))
	if again["replayed"] != true || len(r.ctl.calls) != 1 {
		t.Fatalf("a retry replays and acts once: %v %v", again, r.ctl.calls)
	}
}

func TestARefusalNamesItsGateFromItsWords(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	for said, gate := range map[string]string{"ann: " + TypingElsewhere + "; not starting twice": "terminal_lock", "ann: no room": "state"} {
		r.ctl.ok, r.ctl.said = false, said
		if g := refused(c.call("session.park", map[string]any{"agent": "ann", "request_id": rid(said[:6] + gate)})); g != gate {
			t.Fatalf("%q: gate %q, want %q", said, g, gate)
		}
	}
	r.ctl.said = "late"
	m := c.call("session.wake", map[string]any{"agent": "ann", "request_id": rid("w2")})
	if msg := m["error"].(map[string]any)["message"]; msg != "ann: not woken - late" {
		t.Fatalf("%v", msg)
	}
}

func TestWakeOfAStalledAgentClearsTheStall(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	r.e.DB.Exec("UPDATE agent_state SET state = 'STALLED', failed_attempts = 3 WHERE agent = 'ann'")
	got := result(t, c.call("session.wake", map[string]any{"agent": "ann", "request_id": rid("1")}))
	if got["said"] != "ann: STALLED -> QUEUED" || len(r.ctl.calls) != 0 {
		t.Fatalf("%v %v", got, r.ctl.calls)
	}
	row, _ := db.GetAgentState(r.e.DB, "ann")
	if row.State != "QUEUED" || row.FailedAttempts != 0 {
		t.Fatalf("%+v", row)
	}
}

func TestRestartTakesFlagsAndNoNeedlessLock(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	h := lock.TakeTerminal(r.e.Home, "ann") // a terminal held by a typist does not stop a structured lifecycle verb
	defer h.Release()
	if badField(c.call("session.restart", map[string]any{"agent": "ann", "fresh": "yes", "request_id": rid("2")})) != "fresh" {
		t.Fatal("fresh is a flag")
	}
	result(t, c.call("session.restart", map[string]any{"agent": "ann", "fresh": true, "force": true, "request_id": rid("3")}))
	if r.ctl.calls[0] != "restart ann true true" {
		t.Fatalf("%v", r.ctl.calls)
	}
}

func TestStopTakesAModeAndKillIsForAMemberAndAbove(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	if badField(c.call("session.stop", map[string]any{"agent": "ann", "mode": "halt", "request_id": rid("1")})) != "mode" {
		t.Fatal("mode")
	}
	if badField(c.call("session.stop", map[string]any{"agent": "ann", "mode": "exit", "fresh": true, "request_id": rid("2")})) != "fresh" {
		t.Fatal("fresh is for close only")
	}
	result(t, c.call("session.stop", map[string]any{"agent": "ann", "mode": "close", "fresh": true, "request_id": rid("3")}))
	result(t, c.call("session.stop", map[string]any{"agent": "ann", "mode": "exit", "request_id": rid("3b")}))
	if strings.Join(r.ctl.calls, ";") != "close ann true;close ann false" {
		t.Fatalf("exit and close both close the session: %v", r.ctl.calls)
	}
	if g := refused(r.dial(true).call("session.stop", map[string]any{"agent": "ann", "mode": "kill", "request_id": rid("4")})); g != "role" {
		t.Fatalf("an agent session may not kill: %q", g)
	}
	got := result(t, c.call("session.stop", map[string]any{"agent": "ann", "mode": "kill", "request_id": rid("5")}))
	if got["said"] != "ann: session ended" || len(r.pr.killed) != 1 {
		t.Fatalf("%v %v", got, r.pr.killed)
	}
	r.pr.live["ann"] = false
	if g := refused(c.call("session.stop", map[string]any{"agent": "ann", "mode": "kill", "request_id": rid("6")})); g != "state" {
		t.Fatalf("no structured session: %q", g)
	}
	r.pr.live["ann"], r.pr.err = true, errors.New("a process would not die")
	if g := refused(c.call("session.stop", map[string]any{"agent": "ann", "mode": "kill", "request_id": rid("7")})); g != "state" {
		t.Fatalf("a group that is not gone is a refusal, not a success: %q", g)
	}
}

func TestInterruptIsAControlRequestForAStructuredSessionAndRecordedOnce(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	got := result(t, c.call("session.interrupt", map[string]any{"agent": "ann", "request_id": rid("1")}))
	again := result(t, c.call("session.interrupt", map[string]any{"agent": "ann", "request_id": rid("1")}))
	if got["said"] != "ann: interrupted" || again["replayed"] != true || len(r.pr.interrupted) != 1 {
		t.Fatalf("%v %v %v", got, again, r.pr.interrupted)
	}
	r.pr.live["ann"] = false
	if g := refused(c.call("session.interrupt", map[string]any{"agent": "ann", "request_id": rid("2")})); g != "state" {
		t.Fatalf("a terminal agent has no turn to interrupt from here: %q", g)
	}
	r.pr.live["ann"], r.pr.err = true, errors.New("closed")
	if g := refused(c.call("session.interrupt", map[string]any{"agent": "ann", "request_id": rid("3")})); g != "state" {
		t.Fatalf("an error is a refusal: %q", g)
	}
}

func TestFoldRunsOnceForARequestIdAndARejectedFoldIsRefused(t *testing.T) {
	r := newRig(t)
	var folded []string
	ok, said := true, "ann: folded"
	r.e.Fold = func(a string) (bool, string) { folded = append(folded, a); return ok, said }
	c := r.dial(false)
	got := result(t, c.call("agent.fold", map[string]any{"agent": "ann", "request_id": rid("1")}))
	again := result(t, c.call("agent.fold", map[string]any{"agent": "ann", "request_id": rid("1")}))
	if got["said"] != "ann: folded" || again["replayed"] != true || len(folded) != 1 {
		t.Fatalf("%v %v %v", got, again, folded)
	}
	ok, said = false, "ann: fold rejected - no events to fold"
	if g := refused(c.call("agent.fold", map[string]any{"agent": "ann", "request_id": rid("2")})); g != "state" {
		t.Fatalf("a rejected fold is a refusal: %q", g)
	}
	if m := c.call("agent.fold", map[string]any{"agent": "nobody.here", "request_id": rid("3")}); m["error"] == nil || len(folded) != 2 {
		t.Fatalf("an unknown agent is not folded: %v %v", m, folded)
	}
	r.e.Fold = nil
	if r.dialHas(r.dial(false), "agent.fold") {
		t.Fatal("with no fold port the method is not served")
	}
}

func TestTerminalCloseNeedsAStoppedSessionAndRefusesWhileTyping(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	if g := refused(c.call("terminal.close", map[string]any{"agent": "ann", "request_id": rid("1")})); g != "state" || len(r.closed) != 0 {
		t.Fatalf("a session is running: %q", g)
	}
	r.e.DB.Exec("UPDATE agent_state SET closed_at = 5 WHERE agent = 'ann'")
	h := lock.TakeTerminal(r.e.Home, "ann")
	if g := refused(c.call("terminal.close", map[string]any{"agent": "ann", "request_id": rid("2")})); g != "terminal_lock" {
		t.Fatalf("typing elsewhere: %q", g)
	}
	h.Release()
	got := result(t, c.call("terminal.close", map[string]any{"agent": "ann", "request_id": rid("3")}))
	row, _ := db.GetAgentState(r.e.DB, "ann")
	if got["said"] != "ann: terminal closed" || len(r.closed) != 1 || r.closed[0] != "%1" || row.Terminal.String != "" {
		t.Fatalf("%v %v %+v", got, r.closed, row)
	}
}

func TestWhatTheDesignDroppedIsNotServed(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	for _, m := range []string{"session.input", "session.keys", "session.type", "agent.create"} {
		if r.dialHas(c, m) {
			t.Fatalf("%s is served", m)
		}
	}
}

func (r *rig) dialHas(c *conn, method string) bool {
	m := c.call(method, map[string]any{"agent": "ann", "request_id": rid("x" + method)})
	e, _ := m["error"].(map[string]any)
	return e == nil || e["code"] != float64(-32601)
}

func cursorNames(t *testing.T, d interface {
	Query(string, ...any) (*sql.Rows, error)
}) string {
	t.Helper()
	rows, err := d.Query("SELECT name FROM cursors WHERE name LIKE 'dialog_%' ORDER BY name")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var names []string
	for rows.Next() {
		var n string
		rows.Scan(&n)
		names = append(names, n)
	}
	return strings.Join(names, ",")
}

func seedStall(t *testing.T, d *sql.DB, state string) {
	t.Helper()
	for _, q := range []string{
		"UPDATE agent_state SET state = '" + state + "', detail = 'x', failed_attempts = 3, throttle_count = 2, wake_failures = 1 WHERE agent = 'ann'",
		"INSERT INTO cursors(name, row_id, updated) VALUES('dialog_since:ann', 5, 5), ('dialog_title:ann', 6, 6), ('dialog_since:bob', 7, 7)",
	} {
		if _, err := d.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
}

func TestWakeOfAStalledAgentDropsItsDialogMarkAndNobodyElses(t *testing.T) {
	r := newRig(t)
	seedStall(t, r.e.DB, "STALLED")
	result(t, r.dial(false).call("session.wake", map[string]any{"agent": "ann", "request_id": rid("1")}))
	if got := cursorNames(t, r.e.DB); got != "dialog_since:bob" {
		t.Fatalf("cursors left: %s", got)
	}
}

func TestUnstallOfAnAgentThatIsNotStalledLeavesItsDialogMark(t *testing.T) {
	r := newRig(t)
	seedStall(t, r.e.DB, "RUNNING")
	said, err := r.e.Unstall("ann")
	if err != nil || said != "ann: not STALLED (RUNNING)" {
		t.Fatalf("%q %v", said, err)
	}
	if got := cursorNames(t, r.e.DB); got != "dialog_since:ann,dialog_since:bob,dialog_title:ann" {
		t.Fatalf("cursors: %s", got)
	}
}

// Python's control.unstall, run on a copy of the database Go unstalls, leaves the same state, counters and cursors.
func TestUnstallLeavesWhatPythonsUnstallLeaves(t *testing.T) {
	r := newRig(t)
	seedStall(t, r.e.DB, "STALLED")
	copyFile := filepath.Join(t.TempDir(), "py.sqlite")
	if _, err := r.e.DB.Exec("VACUUM INTO '" + copyFile + "'"); err != nil {
		t.Fatal(err)
	}
	const script = `
import os, sqlite3, sys
sys.path.insert(0, os.getcwd())
from handline.actions import control
con = sqlite3.connect(sys.argv[1]); con.row_factory = sqlite3.Row
print(control.unstall(con, "ann")); con.commit()
`
	root, _ := filepath.Abs("../../..")
	cmd := exec.Command("python3", "-I", "-c", script, copyFile)
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "OCP_HOME="+r.e.Home, "HANDLINE_HOME="+r.e.Home, "HANDLINE_TEST_ROOT="+filepath.Dir(r.e.Home), "OCP_TEST_ROOT="+filepath.Dir(r.e.Home))
	out, err := cmd.CombinedOutput()
	if errors.Is(err, exec.ErrNotFound) {
		t.Skip("no python3")
	}
	if err != nil {
		t.Fatalf("the Python unstall did not run: %v: %s", err, out)
	}
	said, err := r.e.Unstall("ann")
	if err != nil || strings.TrimSpace(string(out)) != said {
		t.Fatalf("said %q (%v), Python %q", said, err, out)
	}
	py, err := sql.Open("sqlite", copyFile)
	if err != nil {
		t.Fatal(err)
	}
	defer py.Close()
	const q = "SELECT state, detail, failed_attempts, throttle_count, wake_failures FROM agent_state WHERE agent = 'ann'"
	var a, b [5]any
	for i, d := range []*sql.DB{r.e.DB, py} {
		got := &a
		if i == 1 {
			got = &b
		}
		if err := d.QueryRow(q).Scan(&got[0], &got[1], &got[2], &got[3], &got[4]); err != nil {
			t.Fatal(err)
		}
	}
	if a != b || cursorNames(t, r.e.DB) != cursorNames(t, py) {
		t.Fatalf("Go %v %s, Python %v %s", a, cursorNames(t, r.e.DB), b, cursorNames(t, py))
	}
}

func (a auth) SessionsReadable() error { return nil }
