package structured

import (
	"context"
	"database/sql"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
)

func testDB(t *testing.T) *sql.DB {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return d
}

func openWith(t *testing.T, l Launch, h Handlers) (*Session, *events) {
	t.Helper()
	ev := &events{}
	h.OnEvent = ev.add
	s, err := Open(context.Background(), l, h, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s, ev
}

func TestARetryAfterARestartIsNotSentAgain(t *testing.T) {
	db := testDB(t)
	led := &DBLedger{DB: db, Agent: "backend"}
	l := launch(t)
	l.Ledger = led
	a, _ := openWith(t, l, Handlers{})
	first, _ := a.Send("op-1", "hello", Now)
	if err := a.WaitConfirmed(ctx(t), "op-1"); err != nil {
		t.Fatal(err)
	}
	a.Close()

	b, ev := openWith(t, l, Handlers{})
	again, err := b.Send("op-1", "hello", Now)
	if err != nil || again.UUID != first.UUID || again.State != Confirmed {
		t.Fatalf("%+v %v", again, err)
	}
	time.Sleep(100 * time.Millisecond)
	if ev.count(isEcho(first.UUID)) != 0 {
		t.Fatal("the retry reached the CLI")
	}
}

func TestASentButUnconfirmedMessageIsUnknownAfterARestartAndAQueuedOneGoesOut(t *testing.T) {
	led := &DBLedger{DB: testDB(t), Agent: "backend"}
	led.Put(Op{ID: "sent", UUID: uuidFor("sent"), Text: "one", Delivery: Now, State: Sent})
	led.Put(Op{ID: "queued", UUID: uuidFor("queued"), Text: "two", Delivery: Queue, State: Queued})
	l := launch(t)
	l.Ledger = led
	s, ev := openWith(t, l, Handlers{})
	if err := s.WaitConfirmed(ctx(t), "queued"); err != nil {
		t.Fatal(err)
	}
	r, _ := s.Send("sent", "one", Now)
	if r.State != Unknown {
		t.Fatalf("a sent message nobody echoed is %v, not unknown", r.State)
	}
	if ev.count(isEcho(uuidFor("sent"))) != 0 || ev.count(isEcho(uuidFor("queued"))) != 1 {
		t.Fatal("the unknown message was written again, or the queued one was not")
	}
	if got, _ := led.Get("queued"); got.State != Confirmed {
		t.Fatalf("ledger: %+v", got)
	}
}

func TestCloseProvesTheProcessGroupIsGone(t *testing.T) {
	old := CloseGrace
	CloseGrace = 200 * time.Millisecond
	t.Cleanup(func() { CloseGrace = old })
	l := launch(t)
	l.Env = append(l.Env, "FAKE_DEAF=1")
	s, _ := openWith(t, l, Handlers{})
	pgid := s.Pid()
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Kill(-pgid, 0); err != syscall.ESRCH {
		t.Fatalf("the group %d is still there: %v", pgid, err)
	}
}

func TestOptionsAreReadBack(t *testing.T) {
	s, _ := open(t, Handlers{})
	if err := s.SetModel(ctx(t), "sonnet"); err != nil {
		t.Fatal(err)
	}
	if err := s.SetModel(ctx(t), "ignored"); err == nil {
		t.Fatal("a model the CLI kept was reported applied")
	}
	if err := s.SetPermissionMode(ctx(t), "plan"); err != nil {
		t.Fatal(err)
	}
	eff, err := s.Settings(ctx(t))
	if err != nil || eff["model"] != "sonnet" {
		t.Fatalf("%v %v", eff, err)
	}
}

func TestFramesBecomeTranscriptEvents(t *testing.T) {
	frame := map[string]any{"type": "assistant", "message": map[string]any{"content": []any{
		map[string]any{"type": "text", "text": "hi"},
		map[string]any{"type": "tool_use", "id": "t1", "name": "Bash", "input": map[string]any{"command": "ls"}}}}}
	got := Translate(frame)
	if len(got) != 2 || got[0].Type != "assistant_text" || got[1].Type != "tool_use" || got[1].Data["name"] != "Bash" {
		t.Fatalf("%+v", got)
	}
	res := Translate(map[string]any{"type": "user", "message": map[string]any{"content": []any{
		map[string]any{"type": "tool_result", "tool_use_id": "t1", "content": "x", "is_error": true}}}})
	if len(res) != 1 || res[0].Type != "tool_result" || res[0].Data["is_error"] != true {
		t.Fatalf("%+v", res)
	}
	if e := Translate(map[string]any{"type": "result", "subtype": "success", "total_cost_usd": 0.5}); len(e) != 1 || e[0].Data["cost_usd"] != 0.5 {
		t.Fatalf("%+v", e)
	}
	if e := Translate(map[string]any{"type": "stream_event"}); e != nil {
		t.Fatalf("%+v", e)
	}
}

func TestAMessageTheCLIReportsQueuedIsConfirmedWithoutAnEcho(t *testing.T) {
	l := launch(t)
	l.Env = append(l.Env, "FAKE_LIFECYCLE_ONLY=1")
	t.Setenv("FAKE_LIFECYCLE_ONLY", "1")
	s, _ := openWith(t, l, Handlers{})
	if _, err := s.Send("op-1", "hello", Now); err != nil {
		t.Fatal(err)
	}
	if err := s.WaitConfirmed(ctx(t), "op-1"); err != nil {
		t.Fatal(err)
	}
}

func TestTheChildSeesWhatTheCLINeedsToFindTheLoginAndNothingElse(t *testing.T) {
	t.Setenv("ANTHROPIC_API_KEY", "secret")
	t.Setenv("USER", "someone")
	for _, kv := range (Launch{ConfigDir: "/c"}).env() {
		if kv == "ANTHROPIC_API_KEY=secret" {
			t.Fatal("the daemon's API key reached the child")
		}
	}
	var user, state bool
	for _, kv := range (Launch{ConfigDir: "/c"}).env() {
		user = user || kv == "USER=someone"
		state = state || kv == "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1"
	}
	if !user || !state {
		t.Fatal("USER and the state-event switch are needed")
	}
}

func TestARunOfApiRetriesIsTimedAndEndsWithTheNextFrame(t *testing.T) {
	clock := time.Unix(1000, 0)
	s := &Session{now: func() time.Time { return clock }}
	retry := map[string]any{"type": "system", "subtype": "api_retry"}
	s.noteRetry(retry)
	clock = clock.Add(90 * time.Second)
	s.noteRetry(retry)
	if got := s.RetryingSince(); got != 1000 {
		t.Fatalf("the run began at 1000, not %d", got)
	}
	s.noteRetry(map[string]any{"type": "assistant"})
	if got := s.RetryingSince(); got != 0 {
		t.Fatalf("output ends the run: %d", got)
	}
}
