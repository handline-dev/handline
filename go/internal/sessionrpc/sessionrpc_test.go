package sessionrpc

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
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
	RegisterTyping(srv, r.e)
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
	r, _, c := typingRig(t)
	if g := refused(c.call("terminal.close", map[string]any{"agent": "ann", "request_id": rid("1")})); g != "state" || len(r.closed) != 0 {
		t.Fatalf("a session is running: %q", g)
	}
	r.e.DB.Exec("UPDATE agent_state SET closed_at = 5 WHERE agent = 'ann'")
	h := lock.TakeTerminal(r.e.Home, "ann")
	if g := refused(c.call("terminal.close", map[string]any{"agent": "ann", "request_id": rid("2")})); g != "terminal_lock" {
		t.Fatalf("typing elsewhere: %q", g)
	}
	h.Release()
	r.e.Holds.Take("ann", "person", "sam")
	if g := refused(c.call("terminal.close", map[string]any{"agent": "ann", "request_id": rid("4")})); g != "input_holder" || len(r.closed) != 0 {
		t.Fatalf("another holds the input: %q", g)
	}
	r.e.Holds.Release("ann")
	r.e.Holds.Take("ann", "person", c.name)
	got := result(t, c.call("terminal.close", map[string]any{"agent": "ann", "request_id": rid("3")}))
	row, _ := db.GetAgentState(r.e.DB, "ann")
	if got["said"] != "ann: terminal closed" || len(r.closed) != 1 || r.closed[0] != "%1" || row.Terminal.String != "" {
		t.Fatalf("%v %v %+v", got, r.closed, row)
	}
	if held, _ := r.e.Holds.Holder("ann"); held != nil {
		t.Fatal("the hold on a closed terminal is over")
	}
	r.e.Holds = nil
	if r.dialHas(r.dial(false), "terminal.close") {
		t.Fatal("served with the typing methods, beside the input holds")
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
	cleared, said, err := r.e.Unstall("ann")
	if err != nil || cleared || said != "ann: not STALLED (RUNNING)" {
		t.Fatalf("%v %q %v", cleared, said, err)
	}
	if got := cursorNames(t, r.e.DB); got != "dialog_since:ann,dialog_since:bob,dialog_title:ann" {
		t.Fatalf("cursors: %s", got)
	}
}

// Unstall leaves the agent queued with the human's word as detail, its three failure counters at 0 and its own dialog marks gone.
func TestUnstallLeavesTheAgentQueuedWithItsCountersAndDialogMarksCleared(t *testing.T) {
	r := newRig(t)
	seedStall(t, r.e.DB, "STALLED")
	cleared, said, err := r.e.Unstall("ann")
	if err != nil || !cleared || said != "ann: STALLED -> QUEUED" {
		t.Fatalf("%v %q %v", cleared, said, err)
	}
	var state, detail string
	var failed, throttle, wake int
	q := "SELECT state, detail, failed_attempts, throttle_count, wake_failures FROM agent_state WHERE agent = 'ann'"
	if err := r.e.DB.QueryRow(q).Scan(&state, &detail, &failed, &throttle, &wake); err != nil {
		t.Fatal(err)
	}
	if state != "QUEUED" || detail != "cleared by a human" || failed != 0 || throttle != 0 || wake != 0 {
		t.Fatalf("%s %q %d %d %d", state, detail, failed, throttle, wake)
	}
	if got := cursorNames(t, r.e.DB); got != "dialog_since:bob" {
		t.Fatalf("cursors: %s", got)
	}
}

func (a auth) SessionsReadable() error { return nil }

func TestAgentUnstallClearsAStallForTheFounderAndRefusesAnAgentThatIsNotStalled(t *testing.T) {
	r := newRig(t)
	c := r.dial(false)
	seedStall(t, r.e.DB, "RUNNING")
	m := c.call("agent.unstall", map[string]any{"agent": "ann", "request_id": rid("u1")})
	if refused(m) != "state" || m["error"].(map[string]any)["message"] != "ann: not STALLED (RUNNING)" {
		t.Fatalf("%v", m)
	}
	r.e.DB.Exec("UPDATE agent_state SET state = 'STALLED' WHERE agent = 'ann'")
	got := result(t, c.call("agent.unstall", map[string]any{"agent": "ann", "request_id": rid("u2")}))
	row, _ := db.GetAgentState(r.e.DB, "ann")
	if got["said"] != "ann: STALLED -> QUEUED" || row.State != "QUEUED" || row.FailedAttempts != 0 || cursorNames(t, r.e.DB) != "dialog_since:bob" {
		t.Fatalf("%v %+v %s", got, row, cursorNames(t, r.e.DB))
	}
	if m := c.call("agent.unstall", map[string]any{"agent": "nobody", "request_id": rid("u3")}); m["error"].(map[string]any)["data"].(map[string]any)["code"] != "not_found" {
		t.Fatalf("%v", m)
	}
	if m := r.dial(true).call("agent.unstall", map[string]any{"agent": "ann", "request_id": rid("u4")}); refused(m) != "role" {
		t.Fatalf("an agent's session cleared a stall: %v", m)
	}
}

func TestAgentMoveIsAPersonsCallAndPassesTheAccountAndWhoAsked(t *testing.T) {
	r := newRig(t)
	var got []string
	r.e.Move = func(call Call, agent, account string) (bool, string, error) {
		got = append(got, fmt.Sprint(agent, " ", account, " ", call.Person))
		return account != "full", "ann moved a -> " + account, nil
	}
	c := r.dial(false)
	res := result(t, c.call("agent.move", map[string]any{"agent": "ann", "account": "b", "request_id": rid("m1")}))
	if res["said"] != "ann moved a -> b" || len(got) != 1 || got[0] != "ann b true" {
		t.Fatalf("%v %v", res, got)
	}
	if m := c.call("agent.move", map[string]any{"agent": "ann", "account": "full", "request_id": rid("m2")}); refused(m) != "state" {
		t.Fatalf("a refusal: %v", m)
	}
	if m := c.call("agent.move", map[string]any{"agent": "ann", "request_id": rid("m3")}); badField(m) != "account" {
		t.Fatalf("%v", m)
	}
	if m := c.call("agent.move", map[string]any{"agent": "nobody", "account": "b", "request_id": rid("m4")}); m["error"].(map[string]any)["data"].(map[string]any)["code"] != "not_found" {
		t.Fatalf("%v", m)
	}
	if m := r.dial(true).call("agent.move", map[string]any{"agent": "ann", "account": "b", "request_id": rid("m5")}); refused(m) != "role" || len(got) != 2 {
		t.Fatalf("an agent's session moved an agent: %v %v", m, got)
	}
}
