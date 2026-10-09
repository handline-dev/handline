package structured

import (
	"context"
	"errors"
	"slices"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

var skew atomic.Int64 // seconds the prompt store's clock runs ahead

type sink struct {
	mu  sync.Mutex
	all []Event
}

func (s *sink) Emit(_ string, ev Event) { s.mu.Lock(); s.all = append(s.all, ev); s.mu.Unlock() }
func (s *sink) snapshot() []Event {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Event(nil), s.all...)
}
func (s *sink) types() (out []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, e := range s.all {
		out = append(out, e.Type)
	}
	return
}

func bash(cmd string) ToolRequest {
	return ToolRequest{ToolName: "Bash", ToolUse: "tu-" + cmd, Input: map[string]any{"command": cmd}}
}

func broker(t *testing.T) (*Broker, *DBPrompts, *sink) {
	t.Helper()
	skew.Store(0)
	p := &DBPrompts{DB: testDB(t), Now: func() time.Time { return time.Now().Add(time.Duration(skew.Load()) * time.Second) }}
	ev := &sink{}
	return &Broker{Policy: func(string) Policy {
		return Policy{Allow: []string{"Read"}, Guard: func(tool string, in any) string {
			if c, _ := in.(map[string]any)["command"].(string); c == "rm -rf /" {
				return "refused by the guard"
			}
			return ""
		}}
	}, Store: p, Events: ev, Timeout: 2 * time.Second, Beat: 20 * time.Millisecond}, p, ev
}

func openRows(t *testing.T, p *DBPrompts) (n int, fp string) {
	t.Helper()
	p.DB.QueryRow("SELECT count(*), coalesce(max(fingerprint), '') FROM prompt_blocks WHERE agent = 'a' AND gone_at IS NULL").Scan(&n, &fp)
	return
}

// waitOpen waits for the one open prompt that is not one of the answered ones (a closed prompt's row stays open until its call returns).
// It polls, so a generous bound costs nothing when the prompt comes at once.
func waitOpen(t *testing.T, p *DBPrompts, answered ...string) string {
	t.Helper()
	for i := 0; i < 1000; i++ {
		if n, fp := openRows(t, p); n == 1 && !slices.Contains(answered, fp) {
			return fp
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("no prompt was recorded")
	return ""
}

func TestThePolicyDecidesBeforeAPersonIsAsked(t *testing.T) {
	b, p, ev := broker(t)
	can := b.CanUseTool("a")
	if d, err := can(context.Background(), ToolRequest{ToolName: "Read"}); err != nil || !d.Allow {
		t.Fatalf("%+v %v", d, err)
	}
	d, err := can(context.Background(), bash("rm -rf /"))
	if err != nil || d.Allow || d.Message != "refused by the guard" {
		t.Fatalf("%+v %v", d, err)
	}
	if n, _ := openRows(t, p); n != 0 {
		t.Fatal("the policy step opened a prompt")
	}
	if got := ev.types(); len(got) != 2 || got[0] != "permission_answer" {
		t.Fatalf("%v", got)
	}
}

func TestAPromptIsAnsweredByPromptAnswer(t *testing.T) {
	b, p, ev := broker(t)
	got := make(chan Decision, 1)
	go func() { d, _ := b.CanUseTool("a")(context.Background(), bash("ls")); got <- d }()
	fp := waitOpen(t, p)
	if !b.Has("a") {
		t.Fatal("the broker does not know the prompt")
	}
	if err := b.Answer("a", "somebody-elses", true, "", "casey"); !errors.Is(err, ErrStale) {
		t.Fatalf("%v", err)
	}
	if err := b.Answer("a", fp, true, "", "casey"); err != nil {
		t.Fatal(err)
	}
	if d := <-got; !d.Allow {
		t.Fatalf("%+v", d)
	}
	if n, _ := openRows(t, p); n != 0 {
		t.Fatal("the row stays open")
	}
	var ok int
	var said string
	p.DB.QueryRow("SELECT answer_ok, coalesce(answer_said, '') FROM prompt_blocks WHERE fingerprint = ?", fp).Scan(&ok, &said)
	if ok != 1 {
		t.Fatalf("answer_ok %d %q", ok, said)
	}
	if err := b.Answer("a", fp, true, "", "casey"); !errors.Is(err, ErrNoPrompt) {
		t.Fatalf("a second answer: %v", err)
	}
	if got := ev.types(); got[0] != "permission_request" || got[1] != "permission_answer" {
		t.Fatalf("%v", got)
	}
}

func TestAWaitingPromptStaysFreshAndNoAnswerIsADeny(t *testing.T) {
	b, p, _ := broker(t)
	b.Timeout = time.Second
	got := make(chan Decision, 1)
	go func() { d, _ := b.CanUseTool("a")(context.Background(), bash("ls")); got <- d }()
	waitOpen(t, p)
	var first, last int64
	p.DB.QueryRow("SELECT first_seen, last_seen FROM prompt_blocks").Scan(&first, &last)
	skew.Store(100)
	var after int64
	for i := 0; i < 500 && after < last+100; i++ { // the next beat refreshes it
		time.Sleep(10 * time.Millisecond)
		p.DB.QueryRow("SELECT last_seen FROM prompt_blocks").Scan(&after)
	}
	if after < last+100 {
		t.Fatalf("the waiting prompt was not refreshed: %d", after)
	}
	d := <-got
	if d.Allow || d.Message == "" {
		t.Fatalf("a timeout must deny: %+v", d)
	}
	if n, _ := openRows(t, p); n != 0 {
		t.Fatal("the row stays open")
	}
}

func TestACancelledPromptOrNoStoreDenies(t *testing.T) {
	b, p, _ := broker(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan Decision, 1)
	go func() { d, _ := b.CanUseTool("a")(ctx, bash("ls")); done <- d }()
	waitOpen(t, p)
	cancel()
	if d := <-done; d.Allow {
		t.Fatalf("%+v", d)
	}
	if n, _ := openRows(t, p); n != 0 {
		t.Fatal("the row stays open")
	}
	b.Store = nil
	if d, err := b.CanUseTool("a")(context.Background(), bash("ls")); err == nil || d.Allow {
		t.Fatalf("no store must not allow: %+v %v", d, err)
	}
}

func TestOnePromptPerAgentAtATime(t *testing.T) {
	b, p, _ := broker(t)
	res := make(chan bool, 2)
	for _, c := range []string{"one", "two"} {
		go func() { d, _ := b.CanUseTool("a")(context.Background(), bash(c)); res <- d.Allow }()
	}
	var answered []string
	for i := 0; i < 2; i++ {
		fp := waitOpen(t, p, answered...)
		if err := b.Answer("a", fp, true, "", "casey"); err != nil {
			t.Fatal(err)
		}
		answered = append(answered, fp)
		<-res
	}
}

// openGate records a prompt's row, then holds Open until released.
type openGate struct {
	PromptStore
	recorded, release chan struct{}
}

func (g openGate) Open(agent string, p PromptRow) (int64, error) {
	id, err := g.PromptStore.Open(agent, p)
	close(g.recorded)
	<-g.release
	return id, err
}

func TestAPromptWhoseRowIsVisibleCanBeAnswered(t *testing.T) {
	b, p, _ := broker(t)
	g := openGate{PromptStore: p, recorded: make(chan struct{}), release: make(chan struct{})}
	b.Store = g
	res := make(chan bool, 1)
	go func() { d, _ := b.CanUseTool("a")(context.Background(), bash("one")); res <- d.Allow }()
	<-g.recorded
	fp := waitOpen(t, p)
	if err := b.Answer("a", fp, true, "", "casey"); err != nil {
		t.Fatalf("the row is open and the answer is refused: %v", err)
	}
	close(g.release)
	if !<-res {
		t.Fatal("the answer was lost")
	}
}

func TestAStructuredAgentEndToEnd(t *testing.T) {
	b, p, ev := broker(t)
	m := &Manager{DB: p.DB, Broker: b, Events: ev}
	if _, err := m.Start(context.Background(), "a", launch(t)); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { m.Stop("a") })
	if !m.Has("a") {
		t.Fatal("no session")
	}
	if _, err := m.Send("a", "op-1", "NEEDPERM", Now); err != nil {
		t.Fatal(err)
	}
	fp := waitOpen(t, p)
	if err := m.Answer("a", fp, true, "", "casey"); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 300; i++ {
		for _, e := range ev.types() {
			if e == "turn_end" {
				goto ended
			}
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("the turn did not end")
ended:
	ev.mu.Lock()
	defer ev.mu.Unlock()
	var sawText bool
	for _, e := range ev.all {
		if e.Type == "assistant_text" && e.Data["text"] == "tool allow" {
			sawText = true
		}
	}
	if !sawText {
		t.Fatalf("the CLI never saw the allow: %+v", ev.all)
	}
	if err := m.Stop("a"); err != nil || m.Has("a") {
		t.Fatalf("stop: %v", err)
	}
}
