package termchan

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/term"
)

// fakeHost is a terminal host with one screen: its rows, cursor and size are set by the test, and what it is sent is kept.
type fakeHost struct {
	mu     sync.Mutex
	rows   []string
	cursor *term.Cursor
	cols   int
	nrows  int
	sent   [][]byte
}

func (h *fakeHost) Name() string                                   { return "fake" }
func (h *fakeHost) Available() bool                                { return true }
func (h *fakeHost) ListTerminals() map[string]bool                 { return map[string]bool{} }
func (h *fakeHost) Send(string, string, bool) (bool, string)       { return true, "" }
func (h *fakeHost) WaitIdle(string, int, func([]string) bool) bool { return true }
func (h *fakeHost) ReadScreen(string, int) *term.Screen {
	h.mu.Lock()
	defer h.mu.Unlock()
	return &term.Screen{Tail: append([]string{}, h.rows...), Source: "screen"}
}
func (h *fakeHost) Cursor(string) *term.Cursor {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.cursor
}
func (h *fakeHost) Size(string) (int, int, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.cols, h.nrows, h.cols > 0
}
func (h *fakeHost) Resize(_ string, cols, rows int) (bool, string) {
	h.mu.Lock()
	h.cols, h.nrows = cols, rows
	h.mu.Unlock()
	return true, ""
}
func (h *fakeHost) SendBytes(_ string, b []byte) (bool, string) {
	h.mu.Lock()
	h.sent = append(h.sent, b)
	h.mu.Unlock()
	return true, ""
}
func (h *fakeHost) ServesBytes() bool        { return true }
func (h *fakeHost) ControlCommand() []string { return nil }
func (h *fakeHost) set(rows ...string) {
	h.mu.Lock()
	h.rows = rows
	h.mu.Unlock()
}

type inbox struct {
	mu   sync.Mutex
	msgs []map[string]any
}

func (i *inbox) send(m map[string]any) bool {
	i.mu.Lock()
	i.msgs = append(i.msgs, m)
	i.mu.Unlock()
	return true
}

func (i *inbox) wait(t *testing.T, n int) []map[string]any {
	t.Helper()
	for end := time.Now().Add(3 * time.Second); time.Now().Before(end); time.Sleep(5 * time.Millisecond) {
		i.mu.Lock()
		if len(i.msgs) >= n {
			out := append([]map[string]any{}, i.msgs...)
			i.mu.Unlock()
			return out
		}
		i.mu.Unlock()
	}
	t.Fatalf("wanted %d notices, got %v", n, i.msgs)
	return nil
}

func texts(v any) []string {
	var out []string
	for _, r := range v.([]any) {
		out = append(out, r.(map[string]any)["text"].(string))
	}
	return out
}

func newHub(h *fakeHost) *Hub { return NewHub(func() Host { return Host{h} }) }

func TestAFrameIsWholeForAClientThatMissedOneAndOnlyTheChangedRowsOtherwise(t *testing.T) {
	h := &fakeHost{rows: []string{"a", "b", "c", "d"}}
	hub := newHub(h)
	in := &inbox{}
	c := NewClient(in.send, func() {})
	ch := hub.Attach("%1", c, "text")
	snap := ch.Snapshot(0, []map[string]any{daemonHolder()})
	if got := texts(snap["lines"]); strings.Join(got, "") != "abcd" || snap["seq"] != 1 {
		t.Fatalf("snapshot %v", snap)
	}
	ch.mu.Lock()
	ch.clients[0].start(ch) // the writer, without the poller: the test offers the frames itself
	ch.mu.Unlock()
	ch.offer([]Row{{Text: "a"}, {Text: "B"}, {Text: "c"}, {Text: "d"}}, nil)
	f := in.wait(t, 1)[0]["params"].(map[string]any)
	if f["full"] != false || len(f["changed"].([]any)) != 1 || f["changed"].([]any)[0].(map[string]any)["y"] != 1 {
		t.Fatalf("one row changed, got %v", f)
	}
	ch.offer([]Row{{Text: "1"}, {Text: "2"}, {Text: "3"}, {Text: "4"}}, nil)
	ch.offer([]Row{{Text: "5"}, {Text: "6"}, {Text: "7"}, {Text: "8"}}, nil) // two frames in a row: the client is a step behind
	f = in.wait(t, 2)[1]["params"].(map[string]any)
	if f["full"] != true || f["lines"] == nil {
		t.Fatalf("a client behind is sent the newest frame whole, got %v", f)
	}
}

func TestRowsThatScrollOffTheTopAreKeptUnderAbsoluteIndexes(t *testing.T) {
	h := &fakeHost{}
	ch := newChannel("%1", "text", newHub(h), func() Host { return Host{h} })
	rows := func(s ...string) []Row {
		var out []Row
		for _, x := range s {
			out = append(out, Row{Text: x})
		}
		return out
	}
	ch.offer(rows("one", "two", "three"), nil)
	ch.offer(rows("two", "three", "four"), nil) // one scrolled off
	ch.offer(rows("three", "four", "five"), nil)
	if got := texts(ch.HistoryPage(10, 10)["lines"]); strings.Join(got, ",") != "one,two" {
		t.Fatalf("history %v", got)
	}
	p := ch.HistoryPage(2, 1)
	if p["first"] != 1 || p["more"] != true || texts(p["lines"])[0] != "two" {
		t.Fatalf("page %v", p)
	}
	ch.offer(rows("x", "y", "z"), nil) // a redraw that scrolls nothing adds nothing
	if got := ch.HistoryPage(10, 10)["lines"]; len(got.([]any)) != 2 {
		t.Fatalf("history grew: %v", got)
	}
}

func TestTheCursorIsCountedInTheFramesOwnRowsAndInCodePoints(t *testing.T) {
	h := &fakeHost{}
	ch := newChannel("%1", "text", newHub(h), func() Host { return Host{h} })
	// A 5-row pane whose bottom two rows are blank: the frame drops them, the cursor sits on the first of them.
	ch.offer([]Row{{Text: "日本x"}, {Text: "b"}, {Text: "c"}}, &term.Cursor{X: 4, Y: 3, Visible: true, Height: 5, BlankBelow: 2})
	got := ch.cursor()
	if got["x"] != 4 || got["y"] != 3 {
		t.Fatalf("the cursor on a blank row the frame drops is past its last row: %v", got)
	}
	ch.offer([]Row{{Text: "日本x"}, {Text: "b"}, {Text: "c"}}, &term.Cursor{X: 4, Y: 0, Visible: true, Height: 5, BlankBelow: 2})
	if got := ch.cursor(); got["x"] != 2 || got["y"] != 0 {
		t.Fatalf("four cells over two wide characters are two code points: %v", got)
	}
	ch.offer([]Row{{Text: "日本x"}, {Text: "b"}, {Text: "c"}}, &term.Cursor{X: 5, Y: 0, Visible: true, Height: 5, BlankBelow: 2})
	if got := ch.cursor(); got["x"] != 3 {
		t.Fatalf("%v", got)
	}
	ch.offer([]Row{{Text: "日本x"}, {Text: "b"}, {Text: "c"}}, &term.Cursor{X: 0, Y: 0, Visible: false, Height: 5})
	if ch.cursor() != nil {
		t.Fatal("a hidden cursor is none")
	}
}

// ---- the control link

// pipeProc is a control-mode process the test plays: what the daemon writes is read from cmds, what tmux says is written to say.
type pipeProc struct {
	cmds chan string    // the daemon's commands, a line each; a real pipe buffers, as this does
	out  *io.PipeReader // tmux's lines
	say  *io.PipeWriter
}

func newPipeProc() *pipeProc {
	or, ow := io.Pipe()
	return &pipeProc{cmds: make(chan string, 100), out: or, say: ow}
}
func (p *pipeProc) Read(b []byte) (int, error) { return p.out.Read(b) }
func (p *pipeProc) Write(b []byte) (int, error) {
	p.cmds <- strings.TrimSuffix(string(b), "\n")
	return len(b), nil
}
func (p *pipeProc) Close() error { p.say.Close(); return nil }
func (p *pipeProc) line(t *testing.T) string {
	t.Helper()
	select {
	case s := <-p.cmds:
		return s
	case <-time.After(3 * time.Second):
		t.Fatal("the daemon sent no command")
		return ""
	}
}
func (p *pipeProc) reply(lines ...string) {
	io.WriteString(p.say, "%begin 1 1 0\n"+strings.Join(lines, "\n")+map[bool]string{true: "\n", false: ""}[len(lines) > 0]+"%end 1 1 0\n")
}
func (p *pipeProc) fail()                    { io.WriteString(p.say, "%begin 1 1 0\nboom\n%error 1 1 0\n") }
func (p *pipeProc) output(pane, data string) { io.WriteString(p.say, "%output "+pane+" "+data+"\n") }

func linkOn(p *pipeProc) *ControlLink {
	return NewControlLink(func() (Proc, error) { return p, nil }, false)
}

func TestOutputIsUnescapedAsBytesAndRepliesComeBackInOrder(t *testing.T) {
	if got := UnescapeOutput([]byte(`a\033[0m\134b\12x`)); string(got) != "a\x1b[0m\\b\\12x" {
		t.Fatalf("%q", got)
	}
	p := newPipeProc()
	l := linkOn(p)
	if !l.Start() {
		t.Fatal("start")
	}
	var mu sync.Mutex
	var got []string
	l.Watch("%1", func(b []byte, ended bool) {
		mu.Lock()
		got = append(got, string(b))
		mu.Unlock()
	})
	done := make(chan []string, 1)
	go func() { done <- l.Command("list-panes", time.Second) }()
	if c := p.line(t); c != "list-panes" {
		t.Fatalf("command %q", c)
	}
	p.output("%1", `hi\015\012`)
	p.reply("one", "two")
	if lines := <-done; len(lines) != 2 || lines[1] != "two" {
		t.Fatalf("reply %v", lines)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(got) != 1 || got[0] != "hi\r\n" {
		t.Fatalf("output %q", got)
	}
}

func TestWhenOneCommandOfALineFailsTmuxRunsNoneOfTheRest(t *testing.T) {
	p := newPipeProc()
	l := linkOn(p)
	l.Start()
	res := make(chan string, 3)
	cb := func(name string) func([]string) {
		return func(lines []string) {
			if lines == nil {
				res <- name + ":nil"
			} else {
				res <- name + ":ok"
			}
		}
	}
	l.BatchAsync([]string{"a", "b", "c"}, []func([]string){cb("a"), cb("b"), cb("c")})
	if c := p.line(t); c != "a ; b ; c" {
		t.Fatalf("one line, got %q", c)
	}
	p.reply("x")
	p.fail()
	var got []string
	for i := 0; i < 3; i++ {
		got = append(got, <-res)
	}
	if strings.Join(got, ",") != "a:ok,b:nil,c:nil" {
		t.Fatalf("%v", got)
	}
}

func modesLine(alt, w, hgt, hist int) string {
	return strings.Join([]string{itoa(alt), "2", "1", "1", itoa(w), itoa(hgt), "0", "0", "0", "0", "0", itoa(hist)}, " ")
}
func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func TestACaptureIsTheModesTheScreenAndTheScrollbackAtOnePlaceInTheStream(t *testing.T) {
	p := newPipeProc()
	l := linkOn(p)
	l.Start()
	got := make(chan *Snapshot, 1)
	live := make(chan bool, 1)
	Capture(l, "%1", 2, func(s *Snapshot) { got <- s }, func() { live <- true })
	if c := p.line(t); !strings.HasPrefix(c, "display-message -p -t %1 '#{alternate_on}") || strings.Count(c, " ; ") != 2 {
		t.Fatalf("command %q", c)
	}
	p.reply(modesLine(0, 20, 3, 5))
	p.reply("top", "mid")
	p.reply("old1", "old2")
	s := <-got
	<-live
	if s.Modes.Rows != 3 || s.Modes.Cols != 20 {
		t.Fatalf("modes %+v", s.Modes)
	}
	// The screen has fewer rows than the pane: the rest are blank rows, and the cursor is placed at the end.
	want := "top\x1b[0m\r\nmid\x1b[0m\r\n\x1b[0m\x1b[2;3H\x1b[?25h"
	if string(s.Data) != want {
		t.Fatalf("%q != %q", s.Data, want)
	}
	if s.HistFirst != 3 || len(s.HistLines) != 2 || s.HistLines[1] != "old2\x1b[0m" || !s.HistMore {
		t.Fatalf("history %+v", s)
	}
}

func TestABytesAttachIsSentOutputOnlyFromTheScreensPlaceOnAndAFloodIsAResync(t *testing.T) {
	p := newPipeProc()
	l := linkOn(p)
	l.Start()
	hub := NewBytesHub()
	ch := hub.Channel("%1", l)
	in := &inbox{}
	c := NewBytesClient(in.send, func() {})
	snapC := make(chan *Snapshot, 1)
	go func() { snapC <- ch.Attach(c, 0) }()
	p.line(t)
	p.output("%1", "before") // before the screen's place: the snapshot has it, the stream does not
	p.reply(modesLine(0, 10, 1, 0))
	p.reply("hello")
	snap := <-snapC
	if snap == nil {
		t.Fatal("no snapshot")
	}
	ch.Begin(c)
	p.output("%1", "after")
	msgs := in.wait(t, 1)
	prm := msgs[0]["params"].(map[string]any)
	if msgs[0]["method"] != "output" || prm["seq"] != 1 {
		t.Fatalf("%v", msgs[0])
	}
	if b, _ := base64.StdEncoding.DecodeString(prm["data"].(string)); string(b) != "after" {
		t.Fatalf("data %q", b)
	}
	hub.Notify("%1", "holder", map[string]any{"holders": []any{}})
	if m := in.wait(t, 2)[1]; m["method"] != "holder" || m["params"].(map[string]any)["seq"] != 2 {
		t.Fatalf("a notice takes the next number: %v", m)
	}
	if !c.putOutput(make([]byte, SlowBytes)) { // a megabyte unsent
		t.Fatal("a client a megabyte behind must resync")
	}
}

// ---- the methods

type rig struct {
	e *Env
	h *fakeHost
}

func newRig(t *testing.T) rig {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HANDLINE_HOME", home)
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	h := &fakeHost{rows: []string{"$ "}, cols: 80, nrows: 24}
	e := New(db, home, func() term.Backend { return h })
	e.Now = func() time.Time { return time.Unix(1_000_000, 0) }
	if _, err := db.Exec("INSERT INTO agent_state(agent, state, updated, terminal) VALUES('ann', 'IDLE', 0, '%1')"); err != nil {
		t.Fatal(err)
	}
	return rig{e, h}
}

func (r rig) agent(t *testing.T) {
	t.Helper()
	dir := filepath.Join(paths.AgentsDir(r.e.Home), "ann")
	if err := mkdir(dir); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte("name: ann\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func person(name string) *rpc.Conn {
	return &rpc.Conn{Caller: rpc.Caller{Role: "member", Info: map[string]any{"kind": "person", "name": name}}}
}

func gate(t *testing.T, err error) string {
	t.Helper()
	e, ok := err.(*rpc.Error)
	if !ok {
		t.Fatalf("want a refusal, got %v", err)
	}
	g, _ := e.Data["gate"].(string)
	return g
}

func TestTakingTheInputIsFirstComeAndReleasingIsTheHoldersAlone(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	out, err := r.e.take(person("pat"), map[string]any{"agent": "ann"})
	if err != nil || out.(map[string]any)["holder"].(map[string]any)["name"] != "pat" {
		t.Fatalf("%v %v", out, err)
	}
	if _, err := r.e.take(person("lee"), map[string]any{"agent": "ann"}); gate(t, err) != "input_holder" {
		t.Fatalf("a second taker is refused: %v", err)
	}
	if _, err := r.e.release(person("lee"), map[string]any{"agent": "ann"}); gate(t, err) != "input_holder" {
		t.Fatalf("only the holder releases: %v", err)
	}
	if _, err := r.e.release(person("pat"), map[string]any{"agent": "ann"}); err != nil {
		t.Fatal(err)
	}
	out, _ = r.e.release(person("pat"), map[string]any{"agent": "ann"})
	if !strings.Contains(out.(map[string]any)["said"].(string), "already holds") {
		t.Fatalf("%v", out)
	}
	var n int
	r.e.DB.QueryRow("SELECT count(*) FROM events WHERE kind = 'input_holder' AND agent = 'ann'").Scan(&n)
	if n != 2 {
		t.Fatalf("a take and a release are events, got %d", n)
	}
}

func TestResizeIsTheHoldersAndTheSizeComesBackWhenTheyLeave(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	p := map[string]any{"terminal": "%1", "cols": json.Number("100"), "rows": json.Number("30")}
	if _, err := r.e.resize(person("pat"), p); gate(t, err) != "input_holder" {
		t.Fatalf("the daemon holding is refused too: %v", err)
	}
	r.e.take(person("pat"), map[string]any{"agent": "ann"})
	if _, err := r.e.resize(person("pat"), p); err != nil {
		t.Fatal(err)
	}
	if r.h.cols != 100 || r.h.nrows != 30 {
		t.Fatalf("size %dx%d", r.h.cols, r.h.nrows)
	}
	inputholdChanged(r.e)
	r.e.release(person("pat"), map[string]any{"agent": "ann"})
	if r.h.cols != 80 || r.h.nrows != 24 {
		t.Fatalf("the size before is given back: %dx%d", r.h.cols, r.h.nrows)
	}
}

func TestInputIsTheHoldersAloneAndLeavesAnEventWithTheCountNotTheBytes(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	b64 := base64.StdEncoding.EncodeToString([]byte("ls\r"))
	p := map[string]any{"terminal": "%1", "data": b64}
	if _, err := r.e.input(person("pat"), p); gate(t, err) != "input_holder" {
		t.Fatalf("held by the daemon: %v", err)
	}
	r.e.take(person("pat"), map[string]any{"agent": "ann"})
	if _, err := r.e.input(person("lee"), p); gate(t, err) != "input_holder" {
		t.Fatalf("held by another: %v", err)
	}
	for _, bad := range []string{"not base64!", "", base64.StdEncoding.EncodeToString(make([]byte, MaxInput+1))} {
		_, err := r.e.input(person("pat"), map[string]any{"terminal": "%1", "data": bad})
		if e, ok := err.(*rpc.Error); !ok || e.RPCCode != rpc.InvalidParams {
			t.Fatalf("%q: %v", bad[:min(len(bad), 12)], err)
		}
	}
}

func TestAttachingTwiceToOneTerminalAndFormatKeepsTheSecond(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	first, second := NewClient((&inbox{}).send, func() {}), NewClient((&inbox{}).send, func() {})
	c := person("pat")
	ch := r.e.Hub.Attach("%1", first, "text")
	r.e.hold(c, "%1", "text", func() { ch.Detach(first) })
	ch2 := r.e.Hub.Attach("%1", second, "text")
	r.e.hold(c, "%1", "text", func() { ch2.Detach(second) })
	ch.mu.Lock()
	n := len(ch.clients)
	ch.mu.Unlock()
	if n != 1 || ch.clients[0] != second {
		t.Fatalf("clients %d", n)
	}
	if _, err := r.e.detach(c, map[string]any{"terminal": "%1", "format": "bytes"}); err != nil {
		t.Fatal(err)
	}
	if r.e.Hub.OpenFor("%1", "text") == nil {
		t.Fatal("detaching bytes leaves text attached")
	}
	r.e.detach(c, map[string]any{"terminal": "%1"})
	if r.e.Hub.OpenFor("%1", "text") != nil {
		t.Fatal("detached")
	}
}

func mkdir(p string) error { return os.MkdirAll(p, 0o755) }

// inputholdChanged is a no-op: Register wires inputhold.Changed, and these tests call the methods directly.
func inputholdChanged(e *Env) { inputhold.Changed = e.holdersChanged }

func TestWidthFollowsPythonsRuleForEmojiAndCombiningMarks(t *testing.T) {
	for r, want := range map[rune]int{'a': 1, '日': 2, '✅': 2, '❌': 2, '⚡': 2, '☕': 2, '⌚': 2, '🚀': 2, '🟢': 2, '́': 0, '‍': 1, '✓': 1} {
		if got := width(r); got != want {
			t.Errorf("width(%q) = %d, want %d", r, got, want)
		}
	}
	if got := codePoints("✅x", 3); got != 2 {
		t.Fatalf("three cells over a wide emoji and x are two code points: %d", got)
	}
	if got := codePoints("éx", 2); got != 3 {
		t.Fatalf("a combining mark rides on its letter: %d", got)
	}
}

// historyHost keeps its own scrollback, as tmux does.
type historyHost struct {
	*fakeHost
	hist []string
}

func (h historyHost) ReadHistory(_ string, before *int, limit int, _ bool) (int, []string, bool) {
	end := len(h.hist)
	if before != nil {
		end = min(*before, end)
	}
	start := max(end-limit, 0)
	return start, h.hist[start:end], true
}

func TestAHostThatKeepsScrollbackIsReadForItAndPolledScreensAddNothing(t *testing.T) {
	h := historyHost{&fakeHost{}, []string{"a", "b", "c"}}
	ch := newChannel("%1", "text", newHub(h.fakeHost), func() Host { return Host{h} })
	ch.offer([]Row{{Text: "one"}, {Text: "two"}}, nil)
	ch.offer([]Row{{Text: "two"}, {Text: "three"}}, nil) // a scroll the host already counts
	snap := ch.Snapshot(2, []map[string]any{{}})["history"].(map[string]any)
	if snap["first"] != 1 || snap["more"] != true || strings.Join(texts(snap["lines"]), ",") != "b,c" {
		t.Fatalf("snapshot history %v", snap)
	}
	p := ch.HistoryPage(2, 5)
	if p["first"] != 0 || p["more"] != false || strings.Join(texts(p["lines"]), ",") != "a,b" {
		t.Fatalf("page %v", p)
	}
}

func TestAFailedBatchDoesNotSkipTheCommandsOfTheNextOne(t *testing.T) {
	p := newPipeProc()
	l := linkOn(p)
	l.Start()
	res := make(chan string, 3)
	cb := func(name string) func([]string) {
		return func(lines []string) { res <- name + map[bool]string{true: ":nil", false: ":ok"}[lines == nil] }
	}
	l.BatchAsync([]string{"a", "b"}, []func([]string){cb("a"), cb("b")})
	l.BatchAsync([]string{"c"}, []func([]string){cb("c")})
	p.line(t)
	p.line(t)
	p.fail()
	p.reply("y")
	var got []string
	for i := 0; i < 3; i++ {
		got = append(got, <-res)
	}
	if strings.Join(got, ",") != "a:nil,b:nil,c:ok" {
		t.Fatalf("%v", got)
	}
}
