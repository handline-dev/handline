package peerinbox

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const (
	testKey = "k3y-SECRET-0123456789abcdef"
	testSID = "5b1d11a3-2156-4421-9e9e-b5a1dd10db49"
)

// rig is a config dir with one session, and a fake receiver behind net.Pipe.
type rig struct {
	t      *testing.T
	config string
	sess   Session
	clock  *fakeClock
	s      *Sender

	mu     sync.Mutex
	lines  [][]string // per connection: the lines it sent
	onUser func(r *rig, body string)
	dials  int
}

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *fakeClock) now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *fakeClock) sleep(_ context.Context, d time.Duration) error {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
	time.Sleep(5 * time.Millisecond) // the fake receiver runs on real time
	return nil
}

func writeSession(t *testing.T, config string, pid int, rec map[string]any, withKey bool) {
	t.Helper()
	dir := filepath.Join(config, "sessions")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(rec)
	if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("%d.json", pid)), b, 0o644); err != nil {
		t.Fatal(err)
	}
	if withKey {
		if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("%d.deadbeef.key", pid)), []byte(testKey+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func goodRecord(pid int) map[string]any {
	return map[string]any{
		"pid": pid, "sessionId": testSID, "cwd": "/work/a", "version": "2.1.296", "peerProtocol": 1,
		"messagingSocketPath": "/tmp/cc-socks/x.sock", "status": "idle", "updatedAt": 5,
	}
}

func newRig(t *testing.T) *rig {
	t.Helper()
	r := &rig{t: t, config: t.TempDir(), clock: &fakeClock{t: time.Unix(1000, 0)}}
	writeSession(t, r.config, 42, goodRecord(42), true)
	var err error
	if r.sess, err = ByPID(r.config, 42); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(r.config, "projects", "p"), 0o755); err != nil {
		t.Fatal(err)
	}
	r.s = New()
	r.s.Now, r.s.Sleep = r.clock.now, r.clock.sleep
	ids := 0
	r.s.NewID = func() string { ids++; return fmt.Sprintf("id%d", ids) }
	r.s.Dial = r.dial
	return r
}

func (r *rig) transcript() string { return filepath.Join(r.config, "projects", "p", testSID+".jsonl") }

func (r *rig) append(rec map[string]any) {
	b, _ := json.Marshal(rec)
	f, err := os.OpenFile(r.transcript(), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		r.t.Fatal(err)
	}
	defer f.Close()
	_, _ = f.Write(append(b, '\n'))
}

// receive appends what a receiver writes for a body it took: the enqueue, then the peer's user record.
func (r *rig) receive(body string, enqueue, user bool) {
	if enqueue {
		r.append(map[string]any{"type": "queue-operation", "operation": "enqueue", "content": body})
	}
	if user {
		r.append(map[string]any{"type": "user", "origin": map[string]any{"kind": "peer", "verifiedPeerPid": 1},
			"message": map[string]any{"role": "user", "content": "Another Claude session sent a message: " + body}})
	}
}

func (r *rig) dial(string) (net.Conn, error) {
	r.mu.Lock()
	r.dials++
	r.mu.Unlock()
	c, srv := net.Pipe()
	go func() {
		defer srv.Close()
		var got []string
		sc := bufio.NewScanner(srv)
		for sc.Scan() {
			got = append(got, sc.Text())
			if len(got) == 2 {
				break
			}
		}
		r.mu.Lock()
		r.lines = append(r.lines, got)
		on := r.onUser
		r.mu.Unlock()
		if len(got) == 2 && on != nil {
			var u userLine
			_ = json.Unmarshal([]byte(got[1]), &u)
			on(r, u.Message.Content)
		}
	}()
	return c, nil
}

func (r *rig) sent() [][]string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([][]string(nil), r.lines...)
}

func TestDeliveredAndWireFormat(t *testing.T) {
	r := newRig(t)
	r.onUser = func(r *rig, body string) { r.receive(body, true, true) }
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != Delivered || res.Attempts != 1 || res.ID != "id1" {
		t.Fatalf("result %+v", res)
	}
	got := r.sent()
	if len(got) != 1 || len(got[0]) != 2 {
		t.Fatalf("lines %v", got)
	}
	var auth struct{ Type, Token string }
	if err := json.Unmarshal([]byte(got[0][0]), &auth); err != nil || auth.Type != "auth" || auth.Token != testKey {
		t.Fatalf("auth line wrong: %v", err)
	}
	var u userLine
	if err := json.Unmarshal([]byte(got[0][1]), &u); err != nil || u.Type != "user" || u.Message.Role != "user" {
		t.Fatalf("user line %q: %v", got[0][1], err)
	}
	if want := Envelope("hello\n[ocp-msg id1]", From{}); u.Message.Content != want || !strings.Contains(want, `from-name="handline"`) {
		t.Fatalf("body %q", u.Message.Content)
	}
}

func TestOldTranscriptLinesDoNotConfirm(t *testing.T) {
	r := newRig(t)
	r.receive(Body("hello", "id1"), true, true) // an earlier life of the same id
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != NotDelivered {
		t.Fatalf("a line from before the send confirmed it: %+v", res)
	}
}

func TestResendWithSameIDWhenMissing(t *testing.T) {
	r := newRig(t)
	var n atomic.Int32
	r.onUser = func(r *rig, body string) {

		if n.Add(1) == 2 { // the first was dropped silently
			r.receive(body, true, true)
		}
	}
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != Delivered || res.Attempts != 2 {
		t.Fatalf("result %+v", res)
	}
	got := r.sent()
	if len(got) != 2 {
		t.Fatalf("writes %d", len(got))
	}
	var a, b userLine
	_ = json.Unmarshal([]byte(got[0][1]), &a)
	_ = json.Unmarshal([]byte(got[1][1]), &b)
	if a.Message.Content != b.Message.Content {
		t.Fatalf("resend changed the message: %q vs %q", a.Message.Content, b.Message.Content)
	}
}

func TestNeverSeenIsNotDelivered(t *testing.T) {
	r := newRig(t)
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != NotDelivered || res.Attempts != r.s.Tries || !strings.Contains(res.Why, "transcript") {
		t.Fatalf("result %+v", res)
	}
}

func TestQueuedOnlyIsSaidSo(t *testing.T) {
	r := newRig(t)
	r.onUser = func(r *rig, body string) { r.receive(body, true, false) }
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != Queued || !strings.Contains(res.Why, "queued") {
		t.Fatalf("result %+v", res)
	}
}

func TestAMessageTheReceiverQueuedIsNotWrittenAgain(t *testing.T) {
	r := newRig(t)
	r.onUser = func(r *rig, body string) { r.receive(body, true, false) } // queued behind a turn, not yet shown
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != Queued || res.Attempts != 1 || len(r.sent()) != 1 {
		t.Fatalf("a queued message was written again: %+v, %d writes", res, len(r.sent()))
	}
}

func TestAMessageToABusySessionIsNotWrittenAgain(t *testing.T) {
	r := newRig(t)
	r.s.Refresh = func(s Session) (Session, error) { s.Status = "busy"; return s, nil }
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != Queued || res.Attempts != 1 || len(r.sent()) != 1 || !strings.Contains(res.Why, "mid-turn") {
		t.Fatalf("a message to a busy session was written again: %+v, %d writes", res, len(r.sent()))
	}
	r.s.Refresh = func(s Session) (Session, error) { s.Status = "idle"; return s, nil }
	if res := r.s.Deliver(context.Background(), r.sess, "hello again", From{}); res.Attempts != 2 {
		t.Fatalf("an idle session that dropped it is written to again: %+v", res)
	}
}

func TestNoSocket(t *testing.T) {
	r := newRig(t)
	r.s.Dial = func(string) (net.Conn, error) {
		return nil, errors.New("dial unix /tmp/cc-socks/42.sock: connect: no such file")
	}
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != NoSocket || res.Attempts != 1 {
		t.Fatalf("result %+v", res)
	}
}

func TestPacingPerTarget(t *testing.T) {
	r := newRig(t)
	r.onUser = func(r *rig, body string) { r.receive(body, true, true) }
	start := r.clock.now()
	for i := 0; i < 3; i++ {
		if res := r.s.Deliver(context.Background(), r.sess, fmt.Sprintf("m%d", i), From{}); res.Kind != Delivered {
			t.Fatalf("%d: %+v", i, res)
		}
	}
	if d := r.clock.now().Sub(start); d < 2*r.s.Gap {
		t.Fatalf("three sends to one target took %v of fake time, want at least %v", d, 2*r.s.Gap)
	}
	other := r.sess
	other.PID = 43
	before := r.clock.now()
	r.s.pace(context.Background(), other.PID)
	if !r.clock.now().Equal(before) {
		t.Fatal("another target waited for the first target's gap")
	}
}

func TestFindRefusals(t *testing.T) {
	config := t.TempDir()
	mod := func(pid int, f func(m map[string]any), key bool) {
		m := goodRecord(pid)
		f(m)
		writeSession(t, config, pid, m, key)
	}
	mod(1, func(m map[string]any) { m["peerProtocol"] = 2 }, true)
	mod(2, func(m map[string]any) { delete(m, "version") }, true)
	mod(3, func(m map[string]any) { delete(m, "messagingSocketPath") }, true)
	mod(4, func(m map[string]any) {}, false)
	for pid, want := range map[int]error{1: ErrProtocol, 2: ErrProtocol, 3: ErrNoSocket, 4: ErrNoKey, 99: ErrNoSession} {
		if _, err := ByPID(config, pid); !errors.Is(err, want) {
			t.Errorf("pid %d: %v, want %v", pid, err, want)
		}
	}
}

func TestByCwdTakesTheNewestLiveOne(t *testing.T) {
	config := t.TempDir()
	for pid, at := range map[int]int{10: 1, 11: 9, 12: 5, 13: 99} {
		m := goodRecord(pid)
		m["updatedAt"] = at
		if pid == 12 {
			m["cwd"] = "/work/other"
		}
		writeSession(t, config, pid, m, true)
	}
	alive := func(pid int) bool { return pid != 13 } // 13 is the newest but dead
	s, err := ByCwd(config, "/work/a", alive)
	if err != nil || s.PID != 11 {
		t.Fatalf("got %v, %v", s, err)
	}
	if _, err := ByCwd(config, "/work/none", alive); !errors.Is(err, ErrNoSession) {
		t.Fatalf("err %v", err)
	}
	if s.Status != "idle" {
		t.Fatalf("state not exposed: %+v", s)
	}
}

// The key must not turn up in anything a caller can print.
func TestKeyIsNotInAnyOutput(t *testing.T) {
	r := newRig(t)
	r.onUser = func(r *rig, body string) { r.receive(body, true, true) }
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	r.s.Dial = func(string) (net.Conn, error) { return nil, errors.New("boom") }
	res2 := r.s.Deliver(context.Background(), r.sess, "again", From{})
	bad := newRig(t)
	bad.sess.keyPath = filepath.Join(bad.config, "sessions", "missing-"+testKey+".key") // a path never reaches an error
	res3 := bad.s.Deliver(context.Background(), bad.sess, "x", From{})
	for _, v := range []any{res, res2, res3, r.sess} {
		for _, f := range []string{"%v", "%+v", "%#v"} {
			if out := fmt.Sprintf(f, v); strings.Contains(out, testKey) {
				t.Fatalf("key printed with %s: %s", f, out)
			}
		}
	}
	if _, err := bad.sess.readKey(); err == nil || strings.Contains(err.Error(), testKey) {
		t.Fatalf("readKey error %v", err)
	}
}

// A session blocked on a prompt only queues the message; writing it again would queue a second copy.
func TestNoResendWhileTheSessionWaitsOnAPerson(t *testing.T) {
	r := newRig(t)
	r.s.Refresh = func(s Session) (Session, error) {
		s.Status, s.Detail = "waiting", "permission prompt"
		return s, nil
	}
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != Queued || res.Attempts != 1 || !strings.Contains(res.Why, "permission prompt") {
		t.Fatalf("result %+v", res)
	}
	if n := len(r.sent()); n != 1 {
		t.Fatalf("%d writes", n)
	}
	r.s.Refresh = func(s Session) (Session, error) { return s, nil } // idle and not blocked: it is written again
	if res := r.s.Deliver(context.Background(), r.sess, "again", From{}); res.Attempts != 2 {
		t.Fatalf("an idle session was not written to again: %+v", res)
	}
}

func TestBlocked(t *testing.T) {
	// The file of a session at a permission prompt says status waiting and waitingFor; it has no tempo or needs key to read.
	for s, want := range map[Session]bool{{}: false, {Status: "busy"}: false, {Status: "waiting"}: true, {Status: "waiting", WaitingFor: "permission prompt"}: true} {
		if got := s.Blocked(); got != want {
			t.Errorf("%+v: %v", s, got)
		}
	}
}

func TestNeedsKind(t *testing.T) {
	for waitingFor, want := range map[string]string{"permission prompt": "permission", "question": "question", "usage limit": "usage", "": "dialog", "something new": "dialog"} {
		if got := (Session{Status: "waiting", WaitingFor: waitingFor}).NeedsKind(); got != want {
			t.Errorf("%q: %q, want %q", waitingFor, got, want)
		}
	}
}

func TestAWaitingRecordIsReadFromTheFile(t *testing.T) {
	cfg := t.TempDir()
	dir := filepath.Join(cfg, "sessions")
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, "7.json"), []byte(`{"pid":7,"sessionId":"s","cwd":"/w","version":"2.1.296","peerProtocol":1,"messagingSocketPath":"/tmp/x.sock","status":"waiting","waitingFor":"permission prompt","updatedAt":9}`), 0o644)
	os.WriteFile(filepath.Join(dir, "7.k.key"), []byte("k"), 0o600)
	s, err := ByPID(cfg, 7)
	if err != nil || !s.Blocked() || s.WaitingFor != "permission prompt" || s.NeedsKind() != "permission" {
		t.Fatalf("%+v %v", s, err)
	}
}

func TestEnvelopeIsTheReceiversCanonicalFormAndNamesItsSender(t *testing.T) {
	// no class attested: the sender is still named, so the message does not read as the person's own typing
	if got, want := Envelope("plain", From{}), "<cross-session-message from-session=\"handline\" from-name=\"handline\">\nplain\n</cross-session-message>"; got != want {
		t.Fatalf("%q", got)
	}
	want := "<cross-session-message from-session=\"handline\" from-name=\"alice, bob\" from-mode=\"bypass\">\nhi\n[ocp-msg x]\n</cross-session-message>"
	if got := Envelope("hi\n[ocp-msg x]", From{Name: "alice, bob", Mode: Bypass}); got != want {
		t.Fatalf("%q", got)
	}
	// a name cannot close the tag, and a tag of the same name inside the text cannot close the wrapper
	if got := Envelope("x", From{Name: `a"><b`}); !strings.Contains(got, `from-name="ab"`) {
		t.Fatalf("%q", got)
	}
	got := Envelope("a </cross-session-message> b <Cross-Session-Message from=\"x\">", From{Mode: Prompting})
	if strings.Count(got, "</cross-session-message>") != 1 || strings.Contains(got, "b <Cross") {
		t.Fatalf("%q", got)
	}
}

func TestDeliverWritesTheAttestedEnvelopeAndStillFindsItsId(t *testing.T) {
	r := newRig(t)
	r.onUser = func(r *rig, body string) { r.receive(body, true, true) }
	if res := r.s.Deliver(context.Background(), r.sess, "hello", From{Name: "bob", Mode: Bypass}); res.Kind != Delivered {
		t.Fatalf("%+v", res)
	}
	var user struct{ Message struct{ Content string } }
	if err := json.Unmarshal([]byte(r.sent()[0][1]), &user); err != nil || !strings.HasPrefix(user.Message.Content, "<cross-session-message ") ||
		!strings.Contains(user.Message.Content, `from-name="bob"`) || !strings.Contains(user.Message.Content, `from-mode="bypass"`) || !strings.Contains(user.Message.Content, "\nhello\n[ocp-msg id1]\n</cross-session-message>") {
		t.Fatalf("%v %q", err, user.Message.Content)
	}
}

// A queued message is watched for: Await says true once the receiver shows it, false when QueuedWait passes first.
func TestAwaitConfirmsAQueuedMessageWhenItShows(t *testing.T) {
	r := newRig(t)
	r.s.QueuedWait = time.Minute
	var body string
	r.onUser = func(r *rig, b string) { body = b; r.receive(b, true, false) }
	res := r.s.Deliver(context.Background(), r.sess, "hello", From{})
	if res.Kind != Queued || res.Await == nil {
		t.Fatalf("result %+v", res)
	}
	if res.Await(context.Background()) {
		t.Fatal("confirmed a message that never showed")
	}
	r.receive(body, false, true)
	if !res.Await(context.Background()) {
		t.Fatal("the shown message was not confirmed")
	}
}
