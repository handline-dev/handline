// Package termchan is the terminal channel: one stream per terminal, served by the daemon, so the IDE and the web UI read no terminal
// of their own (handline/daemon/termchannel.py, termbytes.py, rpc_terminal.py, rpc_input.py).
//
// A Channel is one terminal in the text or styled format while someone is attached. One poller reads its frame at most RateS apart
// however many clients watch, and ends when the last one leaves; detaching never touches the agent. Each client has a writer of its
// own and a bounded queue of the notices that must not be lost (markers, holders, resizes) beside one flag for the frame: a client
// that falls behind on frames is sent the newest frame whole, never a backlog, and one that falls ClientQueue notices behind is hung
// up to attach again, since no frame can make those good. Scrollback is the host's own where it keeps one (tmux), so fast output loses and doubles
// nothing; for a host without it, rows that scroll off the top are kept (HistoryMax) under absolute line indexes, found by matching the old frame's tail against the new one's head; a redraw that scrolls nothing adds nothing.
//
// seq counts every notice of the terminal (frame, resize, marker, holder), so a client can tell what it missed.
package termchan

import (
	"reflect"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/ansi"
	"github.com/handline/handline/go/internal/term"
)

const (
	RateS       = 200 * time.Millisecond // at most ~5 reads a second per terminal
	DefaultRows = 50                     // the rows of a frame when the host does not say how many the terminal has
	HistoryMax  = 5000
	ClientQueue = 200
	FullAbove   = 0.5 // a frame that changes more than this share of its rows goes whole
)

// Row is one screen row: its text, and on a styled channel its style runs.
type Row struct {
	Text  string
	Spans []ansi.Span
}

// Map is the row as the protocol writes it; spans are left out of a row that has none.
func (r Row) Map() map[string]any {
	m := map[string]any{"text": r.Text}
	if len(r.Spans) > 0 {
		spans := make([]any, len(r.Spans))
		for i, s := range r.Spans {
			spans[i] = s.Map()
		}
		m["spans"] = spans
	}
	return m
}

func rowMaps(rows []Row) []any {
	out := make([]any, len(rows))
	for i, r := range rows {
		out[i] = r.Map()
	}
	return out
}

func sameRows(a, b []Row) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].Text != b[i].Text || !reflect.DeepEqual(a[i].Spans, b[i].Spans) {
			return false
		}
	}
	return true
}

// Host is what the channel asks of the terminal host. A host without a method is `unsupported` for the format that needs it.
type Host struct{ Backend term.Backend }

// ReadRows is the bottom rows of the terminal; nil when it cannot be read.
func (h Host) ReadRows(handle string, rows int) ([]Row, bool) {
	scr := h.Backend.ReadScreen(handle, rows)
	if scr == nil || scr.Source != "screen" {
		return nil, false
	}
	out := make([]Row, len(scr.Tail))
	for i, t := range scr.Tail {
		out[i] = Row{Text: t}
	}
	return out, true
}

// ReadStyled is the same rows with their style runs.
func (h Host) ReadStyled(handle string, rows int) ([]Row, bool) {
	r, ok := h.Backend.(term.StyledReader)
	if !ok {
		return nil, false
	}
	raw := r.ReadScreenANSI(handle, rows)
	if raw == nil {
		return nil, false
	}
	out := make([]Row, len(raw))
	for i, line := range raw {
		out[i].Text, out[i].Spans = ansi.Parse(line)
	}
	return out, true
}

// ReadHistory is the host's own scrollback as ReadRows / ReadStyled give rows; ok is false when the host keeps none for the channel to
// read.
func (h Host) ReadHistory(handle, fmt string, before *int, limit int) (int, []Row, bool) {
	r, ok := h.Backend.(term.HistoryReader)
	if !ok {
		return 0, nil, false
	}
	first, raw, ok := r.ReadHistory(handle, before, limit, fmt == "styled")
	if !ok {
		return 0, nil, false
	}
	out := make([]Row, len(raw))
	for i, line := range raw {
		if fmt == "styled" {
			out[i].Text, out[i].Spans = ansi.Parse(line)
		} else {
			out[i].Text = line
		}
	}
	return first, out, true
}

func (h Host) Cursor(handle string) *term.Cursor {
	if r, ok := h.Backend.(term.CursorReader); ok {
		return r.Cursor(handle)
	}
	return nil
}

func (h Host) Size(handle string) (int, int, bool) {
	if s, ok := h.Backend.(term.Sizer); ok {
		return s.Size(handle)
	}
	return 0, 0, false
}

func (h Host) Resize(handle string, cols, rows int) (bool, string) {
	if s, ok := h.Backend.(term.Sizer); ok {
		return s.Resize(handle, cols, rows)
	}
	return false, "the host has no resize"
}

// ServesBytes and ServesStyled say whether the host can give the format at all.
func (h Host) ServesBytes() bool {
	s, ok := h.Backend.(term.ControlSpawner)
	_, sends := h.Backend.(term.BytesSender)
	return ok && sends && s.ServesBytes() && h.Backend.Available()
}

func (h Host) ServesStyled() bool {
	_, ok := h.Backend.(term.StyledReader)
	return ok && h.Backend.Available()
}

func (h Host) SendBytes(handle string, data []byte) (bool, string) {
	return h.Backend.(term.BytesSender).SendBytes(handle, data)
}

// codePoints is how many code points of text the first cells terminal cells hold (a wide character is two cells, a combining one
// none); cells past the end of the text are blanks, one code point each.
func codePoints(text string, cells int) int {
	rs := []rune(text)
	used, i := 0, 0
	for i < len(rs) && used < cells {
		used += width(rs[i])
		i++
		for i < len(rs) && width(rs[i]) == 0 {
			i++
		}
	}
	return i + max(cells-used, 0)
}

func inRanges(r rune, t []rune) bool {
	i := sort.Search(len(t)/2, func(i int) bool { return t[2*i+1] >= r })
	return i < len(t)/2 && t[2*i] <= r
}

func width(r rune) int {
	switch {
	case inRanges(r, combining):
		return 0
	case inRanges(r, wideRanges):
		return 2
	}
	return 1
}

// Client is one attached connection: a writer draining its notices, and the newest frame it still owes.
type Client struct {
	send  func(map[string]any) bool // false: the connection is gone
	close func()

	mu        sync.Mutex
	cond      *sync.Cond
	notices   []map[string]any
	frameOwed bool
	dead      bool

	sentSeq int // the frame seq this client last got; under its channel's lock
	channel *Channel
}

// NewClient is a client that writes with send (false once the connection is gone) and hangs the connection up with closeConn.
func NewClient(send func(map[string]any) bool, closeConn func()) *Client {
	c := &Client{send: send, close: closeConn}
	c.cond = sync.NewCond(&c.mu)
	return c
}

func (c *Client) start(ch *Channel) {
	c.channel = ch
	go c.run()
}

func (c *Client) notice(method string, params map[string]any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.dead {
		return
	}
	if len(c.notices) >= ClientQueue {
		// A frame carries neither the holders nor the size, so what was dropped cannot be made good by one: the client is hung up and
		// attaches again for a snapshot, as a slow subscriber is.
		c.notices, c.dead = nil, true
		c.cond.Broadcast()
		go c.drop()
		return
	}
	c.notices = append(c.notices, map[string]any{"jsonrpc": "2.0", "method": method, "params": params})
	c.cond.Broadcast()
}

// drop hangs the connection up and takes this client off its channel, so a dead one does not keep the poll alive.
func (c *Client) drop() {
	c.close()
	if c.channel != nil {
		c.channel.Detach(c)
	}
}

func (c *Client) oweFrame() {
	c.mu.Lock()
	c.frameOwed = true
	c.mu.Unlock()
	c.cond.Broadcast()
}

func (c *Client) end() {
	c.mu.Lock()
	c.dead = true
	c.mu.Unlock()
	c.cond.Broadcast()
}

func (c *Client) run() {
	for {
		c.mu.Lock()
		for len(c.notices) == 0 && !c.frameOwed && !c.dead {
			c.cond.Wait()
		}
		if c.dead {
			c.mu.Unlock()
			return
		}
		var msg map[string]any
		if len(c.notices) > 0 {
			msg, c.notices = c.notices[0], c.notices[1:]
		} else {
			c.frameOwed = false
		}
		c.mu.Unlock()
		if msg == nil {
			msg = c.channel.frameFor(c)
		}
		if !c.send(msg) {
			c.end()
			c.channel.Detach(c)
			return
		}
	}
}

// Channel is one terminal in one format.
type Channel struct {
	Handle string
	Fmt    string // "text" or "styled"
	hub    *Hub
	host   func() Host

	mu          sync.Mutex
	clients     []*Client
	seq         int
	cur         *term.Cursor // the host's cursor as read; cursor() gives it in the frame's own rows
	lines       []Row
	changed     []int
	cols, rows  int
	history     []Row
	historyFrst int  // the absolute index of history[0]
	hostHistory bool // the host keeps the scrollback, so history above is unused
	polling     bool
}

func newChannel(handle, fmt string, hub *Hub, host func() Host) *Channel {
	ch := &Channel{Handle: handle, Fmt: fmt, hub: hub, host: host, cols: 80, rows: DefaultRows}
	_, _, ch.hostHistory = host().ReadHistory(handle, fmt, nil, 0)
	return ch
}

// Size is the terminal's size as the channel last knew it.
func (ch *Channel) Size() (int, int) {
	ch.mu.Lock()
	defer ch.mu.Unlock()
	return ch.cols, ch.rows
}

func (ch *Channel) setSize(cols, rows int) {
	ch.mu.Lock()
	ch.cols, ch.rows = cols, rows
	ch.mu.Unlock()
}

func (ch *Channel) startPoller() {
	ch.mu.Lock() // two attaches at once must leave one poller
	if ch.polling {
		ch.mu.Unlock()
		return
	}
	ch.polling = true
	ch.mu.Unlock()
	go ch.poll()
}

func (ch *Channel) poll() {
	for {
		started := time.Now()
		ch.mu.Lock()
		if len(ch.clients) == 0 {
			ch.polling = false
			ch.mu.Unlock()
			break
		}
		ch.mu.Unlock()
		if rows, ok := ch.read(); ok {
			ch.offer(rows, ch.host().Cursor(ch.Handle))
		}
		if wait := RateS - time.Since(started); wait > 0 {
			time.Sleep(wait)
		}
	}
	ch.hub.reap(ch.Handle, ch.Fmt) // nobody watches: the channel and its scrollback are dropped
}

func (ch *Channel) read() ([]Row, bool) {
	ch.mu.Lock()
	n := ch.rows
	ch.mu.Unlock()
	if ch.Fmt == "styled" {
		return ch.host().ReadStyled(ch.Handle, n)
	}
	return ch.host().ReadRows(ch.Handle, n)
}

// cursor is {x, y} of the cursor in the frame's own rows, x counted in code points of the row's text as spans are (the host's cells: a
// wide character is two). nil when the host does not say, the cursor is hidden, or it is above the frame. y may be len(lines) or more:
// the cursor on a blank row the frame drops. Held under ch.mu.
func (ch *Channel) cursor() map[string]any {
	c, n := ch.cur, len(ch.lines)
	if c == nil || !c.Visible {
		return nil
	}
	y := c.Y - (c.Height - 1 - c.BlankBelow) + (n - 1)
	if y < 0 {
		return nil
	}
	text := ""
	if y < n {
		text = ch.lines[y].Text
	}
	return map[string]any{"x": codePoints(text, c.X), "y": y}
}

func (ch *Channel) offer(rows []Row, cur *term.Cursor) {
	ch.mu.Lock()
	defer ch.mu.Unlock()
	if sameRows(rows, ch.lines) {
		before := ch.cursor()
		ch.cur = cur
		if !reflect.DeepEqual(ch.cursor(), before) { // the cursor moved over the same rows: a frame with nothing changed, only the cursor
			ch.changed = nil
			ch.seq++
			for _, c := range ch.clients {
				c.oweFrame()
			}
		}
		return
	}
	ch.cur = cur
	if !ch.hostHistory {
		ch.scrolled(ch.lines, rows)
	}
	prev := ch.lines
	ch.lines = rows
	n := max(len(rows), len(prev))
	ch.changed = nil
	for y := 0; y < n; y++ {
		if y >= len(rows) || y >= len(prev) || rows[y].Text != prev[y].Text || !reflect.DeepEqual(rows[y].Spans, prev[y].Spans) {
			ch.changed = append(ch.changed, y)
		}
	}
	ch.seq++
	for _, r := range rows {
		ch.cols = max(ch.cols, utf8.RuneCountInString(r.Text))
	}
	for _, c := range ch.clients {
		c.oweFrame()
	}
}

// scrolled keeps the rows that left the top: the smallest k such that the old frame from row k on is the new frame's start. A
// heuristic: a screen that happens to redraw with the old tail as its head is read as a scroll.
func (ch *Channel) scrolled(old, now []Row) {
	if len(old) == 0 || len(now) == 0 {
		return
	}
	for k := 1; k < len(old); k++ {
		tail := old[k:]
		if len(now) < len(tail) || !sameRows(now[:len(tail)], tail) {
			continue
		}
		seen := false
		for _, r := range old[:k] {
			if strings.TrimSpace(r.Text) != "" {
				seen = true
				break
			}
		}
		if !seen {
			continue
		}
		ch.history = append(ch.history, old[:k]...)
		if drop := len(ch.history) - HistoryMax; drop > 0 {
			ch.history = ch.history[drop:]
			ch.historyFrst += drop
		}
		return
	}
}

// frameFor is the frame notice a client is owed now: the rows that changed when it is one step behind, else the whole frame.
func (ch *Channel) frameFor(c *Client) map[string]any {
	ch.mu.Lock()
	defer ch.mu.Unlock()
	incremental := c.sentSeq == ch.seq-1 && ch.seq > 1 && float64(len(ch.changed)) <= FullAbove*float64(max(len(ch.lines), 1))
	c.sentSeq = ch.seq
	params := map[string]any{"terminal": ch.Handle, "seq": ch.seq, "full": !incremental, "cursor": ch.cursor()}
	if ch.Fmt == "styled" {
		params["format"] = "styled"
	}
	if incremental {
		changed := []any{}
		for _, y := range ch.changed {
			row := Row{}
			if y < len(ch.lines) {
				row = ch.lines[y]
			}
			changed = append(changed, map[string]any{"y": y, "row": row.Map()})
		}
		params["changed"] = changed
	} else {
		params["lines"] = rowMaps(ch.lines)
	}
	return map[string]any{"jsonrpc": "2.0", "method": "frame", "params": params}
}

// Snapshot is the reply to terminal.attach: the frame and one page of history.
func (ch *Channel) Snapshot(history int, holders []map[string]any) map[string]any {
	var hostLines []Row
	hostFirst, got := 0, false
	if ch.hostHistory {
		hostFirst, hostLines, got = ch.host().ReadHistory(ch.Handle, ch.Fmt, nil, max(history, 0))
	}
	ch.mu.Lock()
	defer ch.mu.Unlock()
	lines, first, floor := hostLines, hostFirst, 0
	if !got {
		lines = nil
		if history > 0 {
			lines = ch.history[max(len(ch.history)-history, 0):]
		}
		first, floor = ch.historyFrst+len(ch.history)-len(lines), ch.historyFrst
	}
	return map[string]any{"terminal": ch.Handle, "format": ch.Fmt, "seq": ch.seq, "cols": ch.cols, "rows": ch.rows,
		"lines": rowMaps(ch.lines), "cursor": ch.cursor(),
		"history": map[string]any{"first": first, "lines": rowMaps(lines), "more": first > floor},
		"holder":  holders[0], "holders": holders}
}

// HistoryPage is the scrollback lines [before-limit, before) by absolute index.
func (ch *Channel) HistoryPage(before, limit int) map[string]any {
	if ch.hostHistory {
		if first, lines, ok := ch.host().ReadHistory(ch.Handle, ch.Fmt, &before, limit); ok {
			return map[string]any{"first": first, "lines": rowMaps(lines), "more": first > 0}
		}
	}
	ch.mu.Lock()
	defer ch.mu.Unlock()
	end := max(min(before, ch.historyFrst+len(ch.history)), ch.historyFrst)
	start := max(end-limit, ch.historyFrst)
	lines := ch.history[start-ch.historyFrst : end-ch.historyFrst]
	return map[string]any{"first": start, "lines": rowMaps(lines), "more": start > ch.historyFrst}
}

func (ch *Channel) attach(c *Client) {
	ch.mu.Lock()
	first := len(ch.clients) == 1
	c.sentSeq = ch.seq
	empty := len(ch.lines) == 0
	ch.mu.Unlock()
	if first || empty {
		if cols, rows, ok := ch.host().Size(ch.Handle); ok {
			ch.mu.Lock()
			ch.cols, ch.rows = cols, rows
			ch.mu.Unlock()
		}
		if rows, ok := ch.read(); ok {
			ch.offer(rows, ch.host().Cursor(ch.Handle))
		}
	}
	ch.mu.Lock()
	c.sentSeq = ch.seq // the snapshot already carries it
	ch.mu.Unlock()
	c.mu.Lock()
	c.frameOwed = false
	c.mu.Unlock()
}

// Begin starts the writer and the poller once the reply to terminal.attach is out.
func (ch *Channel) Begin(c *Client) {
	c.start(ch)
	ch.startPoller()
}

// Detach takes c off the channel and ends it.
func (ch *Channel) Detach(c *Client) {
	ch.mu.Lock()
	for i, x := range ch.clients {
		if x == c {
			ch.clients = append(ch.clients[:i:i], ch.clients[i+1:]...)
			break
		}
	}
	ch.mu.Unlock()
	c.end()
}

// Notify sends a notice to every client in the terminal's own sequence.
func (ch *Channel) Notify(method string, params map[string]any) int {
	ch.mu.Lock()
	defer ch.mu.Unlock()
	ch.seq++
	for _, c := range ch.clients {
		p := map[string]any{"terminal": ch.Handle, "seq": ch.seq}
		for k, v := range params {
			p[k] = v
		}
		c.notice(method, p)
	}
	return ch.seq
}

// Hub holds the open channels by terminal handle, text ones and styled ones apart. A channel lives while someone is attached and goes
// with its poller (reap).
type Hub struct {
	host func() Host

	mu      sync.Mutex
	text    map[string]*Channel
	styled  map[string]*Channel
	restore map[string][2]int // handle -> the size before a resize, put back when its holder leaves
}

// NewHub is a hub whose channels read the terminals of host().
func NewHub(host func() Host) *Hub {
	return &Hub{host: host, text: map[string]*Channel{}, styled: map[string]*Channel{}, restore: map[string][2]int{}}
}

func (h *Hub) store(fmt string) map[string]*Channel {
	if fmt == "styled" {
		return h.styled
	}
	return h.text
}

// OpenFor is the channel of handle in fmt if someone is attached to it.
func (h *Hub) OpenFor(handle, fmt string) *Channel {
	h.mu.Lock()
	ch := h.store(fmt)[handle]
	h.mu.Unlock()
	if ch == nil {
		return nil
	}
	ch.mu.Lock()
	defer ch.mu.Unlock()
	if len(ch.clients) == 0 {
		return nil
	}
	return ch
}

// OpenAll is the text and the styled channel of handle, those with someone attached.
func (h *Hub) OpenAll(handle string) []*Channel {
	var out []*Channel
	for _, f := range []string{"text", "styled"} {
		if ch := h.OpenFor(handle, f); ch != nil {
			out = append(out, ch)
		}
	}
	return out
}

// Attach is the channel of handle in fmt with c on it, made and joined under the hub's lock so reap cannot drop it between.
func (h *Hub) Attach(handle string, c *Client, fmt string) *Channel {
	h.mu.Lock()
	st := h.store(fmt)
	ch := st[handle]
	if ch == nil {
		ch = newChannel(handle, fmt, h, h.host)
		st[handle] = ch
	}
	ch.mu.Lock()
	ch.clients = append(ch.clients, c)
	ch.mu.Unlock()
	h.mu.Unlock()
	ch.attach(c)
	return ch
}

func (h *Hub) reap(handle, fmt string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	st := h.store(fmt)
	if ch := st[handle]; ch != nil {
		ch.mu.Lock()
		if len(ch.clients) == 0 && !ch.polling {
			delete(st, handle)
		}
		ch.mu.Unlock()
	}
}

// Restore remembers the size a terminal had before its holder resized it, unless one is remembered already.
func (h *Hub) Restore(handle string, cols, rows int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, ok := h.restore[handle]; !ok {
		h.restore[handle] = [2]int{cols, rows}
	}
}

func (h *Hub) takeRestore(handle string) ([2]int, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	v, ok := h.restore[handle]
	delete(h.restore, handle)
	return v, ok
}
