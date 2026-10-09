package termchan

// The terminal channel's bytes format on tmux (contract x-terminal-bytes; handline/daemon/termbytes.py): the pane's own escape-coded
// output, as base64 notices.
//
// One control-mode client (`tmux -C`) is the daemon's window onto the server: its `%output` lines are the panes' output in order, and
// the commands it is given are answered in the same stream. That ordering is what makes a snapshot exact: the screen is captured by a
// command on that client, the reply stands at one place among the `%output` lines, and a bytes attach starts streaming from there, so
// no byte is lost or repeated between the snapshot and the first notice.
//
// An attach has one counter, seq, that goes up by exactly one with each notice it is sent (output, snapshot, marker, holder); its
// snapshot's seq is the last notice the screen includes, which is 0 for a fresh attach. Output is queued per client and sent in notices
// of at most 64 KiB, at most one every 50 ms. Nothing is dropped from the middle: when one client's unsent output reaches 1 MiB it is
// all discarded and a snapshot notice, reason slow_client, takes its place (counting bytes, not seconds).

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"fmt"
	"io"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	OutputEvery    = 50 * time.Millisecond
	OutputMax      = 64 * 1024
	SlowBytes      = 1024 * 1024
	SnapshotMax    = 1024 * 1024
	CommandTimeout = 15 * time.Second
)

var sgrRE = regexp.MustCompile("\x1b\\[[0-9;:]*m")

// UnescapeOutput is the bytes of a control-mode %output payload: tmux writes bytes below 0x20 and the backslash as \ooo (octal), every
// other byte as it is (so a payload may end inside a multi-byte character, and is read as bytes, never as text).
func UnescapeOutput(raw []byte) []byte {
	out := make([]byte, 0, len(raw))
	for i := 0; i < len(raw); {
		if raw[i] == '\\' && len(raw)-i >= 4 && isOctal(raw[i+1:i+4]) {
			v, _ := strconv.ParseUint(string(raw[i+1:i+4]), 8, 16)
			out = append(out, byte(v))
			i += 4
		} else {
			out = append(out, raw[i])
			i++
		}
	}
	return out
}

func isOctal(b []byte) bool {
	for _, c := range b {
		if c < '0' || c > '7' {
			return false
		}
	}
	return true
}

// lossy is s with each byte that is not valid UTF-8 as U+FFFD, one for one.
func lossy(b []byte) string {
	if utf8.Valid(b) {
		return string(b)
	}
	var sb strings.Builder
	for len(b) > 0 {
		r, n := utf8.DecodeRune(b)
		if r == utf8.RuneError && n == 1 {
			sb.WriteRune(utf8.RuneError)
		} else {
			sb.Write(b[:n])
		}
		b = b[n:]
	}
	return sb.String()
}

// Proc is the control-mode process: lines out, commands in. Tests give a fake.
type Proc interface {
	io.Reader
	io.Writer
	Close() error
}

type execProc struct {
	cmd    *exec.Cmd
	in     io.WriteCloser
	out    io.ReadCloser
	closed sync.Once
}

func (p *execProc) Read(b []byte) (int, error)  { return p.out.Read(b) }
func (p *execProc) Write(b []byte) (int, error) { return p.in.Write(b) }

// Close ends the client once: Stop and the reader reaching its end both close it, and a second cmd.Wait races the first.
func (p *execProc) Close() error {
	p.closed.Do(func() {
		p.in.Close()
		if p.cmd.Process != nil {
			p.cmd.Process.Kill()
		}
		go p.cmd.Wait()
	})
	return nil
}

// SpawnCommand starts argv as the control-mode client.
func SpawnCommand(argv []string) func() (Proc, error) {
	return func() (Proc, error) {
		cmd := exec.Command(argv[0], argv[1:]...)
		in, err := cmd.StdinPipe()
		if err != nil {
			return nil, err
		}
		out, err := cmd.StdoutPipe()
		if err != nil {
			return nil, err
		}
		if err := cmd.Start(); err != nil {
			return nil, err
		}
		return &execProc{cmd: cmd, in: in, out: out}, nil
	}
}

// batch identifies the commands sent on one line; it has a byte so each is a distinct address (a zero-size value shares one).
type batch struct{ _ byte }

type pendingCmd struct {
	cb    func([]string) // nil lines: an error or a dead link
	group *batch
}

// Handler takes the bytes of one %output of a pane; nil when the link ended.
type Handler struct{ fn func(data []byte, ended bool) }

// ControlLink is a control-mode connection: commands out, replies and %output in, in the order tmux sends them.
type ControlLink struct {
	spawn   func() (Proc, error)
	greeted bool // real tmux sends one empty reply block, unasked, as it attaches

	mu       sync.Mutex
	proc     Proc
	alive    bool
	pending  []pendingCmd // the commands sent and not yet answered, in order
	handlers map[string][]*Handler
}

// NewControlLink is a link that spawns its process with spawn. greeted: the process sends the unasked empty block (real tmux does).
func NewControlLink(spawn func() (Proc, error), greeted bool) *ControlLink {
	return &ControlLink{spawn: spawn, greeted: greeted, handlers: map[string][]*Handler{}}
}

// Start connects the link if it is not; false when the host could not be reached.
func (l *ControlLink) Start() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.alive {
		return true
	}
	p, err := l.spawn()
	if err != nil {
		return false
	}
	l.proc, l.alive = p, true
	if l.greeted {
		l.pending = append(l.pending, pendingCmd{func([]string) {}, nil}) // takes that block, so the first command gets its own reply
	}
	go l.read(p)
	return true
}

// Stop ends the link.
func (l *ControlLink) Stop() {
	l.mu.Lock()
	l.alive = false
	p := l.proc
	l.proc = nil
	l.mu.Unlock()
	if p != nil {
		p.Close()
	}
}

// CommandAsync sends command; cb is called on the reader, in order with every %output, with the reply's lines (nil on an error or a
// dead link).
func (l *ControlLink) CommandAsync(command string, cb func([]string)) {
	l.BatchAsync([]string{command}, []func([]string){cb})
}

// BatchAsync sends commands as one line (`a ; b ; c`), which tmux runs without taking any output between them: what they read is the
// pane at one place in the stream. Each has its own reply, in order; when one fails tmux runs none of the rest, and each of those is
// answered nil.
func (l *ControlLink) BatchAsync(commands []string, cbs []func([]string)) {
	var group *batch
	if len(commands) > 1 {
		group = new(batch)
	}
	l.mu.Lock()
	var dead []func([]string)
	if !l.alive {
		dead = cbs
	} else {
		for _, cb := range cbs {
			l.pending = append(l.pending, pendingCmd{cb, group})
		}
		if _, err := l.proc.Write([]byte(strings.Join(commands, " ; ") + "\n")); err != nil {
			l.pending = l.pending[:len(l.pending)-len(cbs)]
			dead = cbs
		}
	}
	l.mu.Unlock()
	for _, cb := range dead {
		cb(nil)
	}
}

// Command runs command and waits for its reply; nil on a timeout, an error or a dead link.
func (l *ControlLink) Command(command string, timeout time.Duration) []string {
	got := make(chan []string, 1)
	l.CommandAsync(command, func(lines []string) { got <- lines })
	select {
	case lines := <-got:
		return lines
	case <-time.After(timeout):
		return nil
	}
}

// Watch calls fn with each %output of pane.
func (l *ControlLink) Watch(pane string, fn func(data []byte, ended bool)) *Handler {
	h := &Handler{fn}
	l.mu.Lock()
	l.handlers[pane] = append(l.handlers[pane], h)
	l.mu.Unlock()
	return h
}

func (l *ControlLink) Unwatch(pane string, h *Handler) {
	l.mu.Lock()
	defer l.mu.Unlock()
	hs := l.handlers[pane]
	for i, x := range hs {
		if x == h {
			hs = append(hs[:i:i], hs[i+1:]...)
			break
		}
	}
	if len(hs) == 0 {
		delete(l.handlers, pane)
	} else {
		l.handlers[pane] = hs
	}
}

func (l *ControlLink) read(p Proc) {
	r := bufio.NewReaderSize(p, 64*1024)
	var block []string
	inBlock := false
	for {
		raw, err := r.ReadBytes('\n')
		if len(raw) > 0 {
			raw = bytes.TrimSuffix(raw, []byte("\n"))
			switch {
			case inBlock:
				if bytes.HasPrefix(raw, []byte("%end ")) || bytes.HasPrefix(raw, []byte("%error ")) {
					failed := bytes.HasPrefix(raw, []byte("%error "))
					l.mu.Lock()
					var cb pendingCmd
					have := len(l.pending) > 0
					if have {
						cb, l.pending = l.pending[0], l.pending[1:]
					}
					var skipped []pendingCmd
					for failed && cb.group != nil && len(l.pending) > 0 && l.pending[0].group == cb.group {
						skipped = append(skipped, l.pending[0]) // tmux ran none of the rest of the line
						l.pending = l.pending[1:]
					}
					l.mu.Unlock()
					lines := block
					if lines == nil {
						lines = []string{}
					}
					inBlock, block = false, nil
					if have {
						if failed {
							cb.cb(nil)
						} else {
							cb.cb(lines)
						}
					}
					for _, s := range skipped {
						s.cb(nil)
					}
				} else {
					block = append(block, lossy(raw))
				}
			case bytes.HasPrefix(raw, []byte("%begin ")):
				inBlock, block = true, nil
			case bytes.HasPrefix(raw, []byte("%output ")):
				parts := bytes.SplitN(raw, []byte(" "), 3)
				pane, data := string(parts[1]), []byte(nil)
				if len(parts) == 3 {
					data = parts[2]
				}
				l.mu.Lock()
				hs := append([]*Handler(nil), l.handlers[pane]...)
				l.mu.Unlock()
				for _, h := range hs {
					h.fn(UnescapeOutput(data), false)
				}
			}
		}
		if err != nil {
			break
		}
	}
	l.mu.Lock()
	if l.proc == p {
		l.alive = false
		l.proc = nil
	}
	pending := l.pending
	l.pending = nil
	var all []*Handler
	for _, hs := range l.handlers {
		all = append(all, hs...)
	}
	l.mu.Unlock()
	p.Close()
	for _, c := range pending {
		c.cb(nil)
	}
	for _, h := range all {
		h.fn(nil, true) // the link ended: each channel snapshots again or ends its clients
	}
}

// ---------------------------------------------------------------- the snapshot

const modeFormat = "#{alternate_on} #{cursor_x} #{cursor_y} #{cursor_flag} #{pane_width} #{pane_height} #{keypad_cursor_flag} " +
	"#{mouse_any_flag} #{mouse_button_flag} #{mouse_standard_flag} #{mouse_sgr_flag} #{history_size}"

// Modes is what the pane says of itself.
type Modes struct {
	AltScreen, CursorVisible, AppCursor, MouseAny, MouseButton, MouseStandard, MouseSGR bool
	CursorX, CursorY, Cols, Rows, HistorySize                                           int
}

func parseModes(lines []string) *Modes {
	if len(lines) == 0 {
		return nil
	}
	parts := strings.Fields(lines[0])
	if len(parts) != 12 {
		return nil
	}
	n := make([]int, 12)
	for i, p := range parts {
		v, err := strconv.Atoi(p)
		if err != nil || v < 0 || p[0] == '+' {
			return nil
		}
		n[i] = v
	}
	return &Modes{AltScreen: n[0] != 0, CursorX: n[1], CursorY: n[2], CursorVisible: n[3] != 0, Cols: n[4], Rows: n[5], AppCursor: n[6] != 0,
		MouseAny: n[7] != 0, MouseButton: n[8] != 0, MouseStandard: n[9] != 0, MouseSGR: n[10] != 0, HistorySize: n[11]}
}

// snapshotData is the bytes that paint the screen on a freshly reset terminal of the pane's size: the alternate screen if it is on, the
// rows with their styling, the modes that change what a client sends, then the cursor. Styling is left out of a screen whose coded
// form passes SnapshotMax.
func snapshotData(m *Modes, rows []string) []byte {
	build := func(lines []string) []byte {
		var sb strings.Builder
		if m.AltScreen {
			sb.WriteString("\x1b[?1049h")
		}
		for i, r := range lines {
			if i > 0 {
				sb.WriteString("\r\n")
			}
			sb.WriteString(r + "\x1b[0m")
		}
		if m.AppCursor {
			sb.WriteString("\x1b[?1h")
		}
		switch {
		case m.MouseAny:
			sb.WriteString("\x1b[?1003h")
		case m.MouseButton:
			sb.WriteString("\x1b[?1002h")
		case m.MouseStandard:
			sb.WriteString("\x1b[?1000h")
		}
		if m.MouseSGR {
			sb.WriteString("\x1b[?1006h")
		}
		fmt.Fprintf(&sb, "\x1b[%d;%dH", m.CursorY+1, m.CursorX+1)
		if m.CursorVisible {
			sb.WriteString("\x1b[?25h")
		} else {
			sb.WriteString("\x1b[?25l")
		}
		return []byte(sb.String())
	}
	data := build(rows)
	if len(data) <= SnapshotMax {
		return data
	}
	plain := make([]string, len(rows))
	for i, r := range rows {
		plain[i] = sgrRE.ReplaceAllString(r, "")
	}
	return build(plain)
}

// Snapshot is a pane's screen at one place in the stream.
type Snapshot struct {
	Modes     *Modes
	Data      []byte
	HistFirst int
	HistLines []string
	HistMore  bool
}

func (s *Snapshot) history() map[string]any {
	lines := make([]any, len(s.HistLines))
	for i, l := range s.HistLines {
		lines[i] = l
	}
	return map[string]any{"first": s.HistFirst, "lines": lines, "more": s.HistMore}
}

// encode is the snapshot's part of a reply or notice.
func (s *Snapshot) encode() map[string]any {
	m := s.Modes
	return map[string]any{"cols": m.Cols, "rows": m.Rows, "data": base64.StdEncoding.EncodeToString(s.Data), "alt_screen": m.AltScreen,
		"cursor": map[string]any{"x": m.CursorX, "y": m.CursorY}, "cursor_visible": m.CursorVisible}
}

// Capture asks for the screen of handle now, on the control link so that it stands in order among the output: onReady runs on the
// reader with the snapshot or nil. The modes, the screen and the scrollback are asked in one command line, so they are the pane at
// one place in the stream; onLive runs at the screen's reply, in order with the output, which is where a client owed the stream starts.
// history is how many scrollback lines to ask for.
func Capture(l *ControlLink, handle string, history int, onReady func(*Snapshot), onLive func()) {
	commands := []string{fmt.Sprintf("display-message -p -t %s '%s'", handle, modeFormat), fmt.Sprintf("capture-pane -p -e -t %s", handle)}
	if history > 0 {
		commands = append(commands, fmt.Sprintf("capture-pane -p -e -t %s -S -%d -E -1", handle, history))
	}
	var modes *Modes
	var rows []string
	haveRows := false
	done := func(hist []string) {
		if modes == nil || !haveRows {
			onReady(nil)
			return
		}
		var screen []string
		if len(rows) >= modes.Rows {
			screen = rows[len(rows)-modes.Rows:]
		} else {
			screen = append(append([]string{}, rows...), make([]string, modes.Rows-len(rows))...)
		}
		snap := &Snapshot{Modes: modes, Data: snapshotData(modes, screen)}
		total := modes.HistorySize
		n := 0
		if !modes.AltScreen {
			n = min(history, total)
		}
		var lines []string
		if n > 0 {
			for _, ln := range hist {
				lines = append(lines, ln+"\x1b[0m")
			}
			if len(lines) > n {
				lines = lines[len(lines)-n:]
			}
		}
		snap.HistFirst = total - len(lines)
		snap.HistLines = lines
		snap.HistMore = total-len(lines) > 0 && !modes.AltScreen
		onReady(snap)
	}
	cbs := []func([]string){
		func(lines []string) { modes = parseModes(lines) },
		func(r []string) {
			rows, haveRows = r, r != nil
			if haveRows && modes != nil && onLive != nil {
				onLive()
			}
			if len(commands) == 2 {
				done(nil)
			}
		},
		func(lines []string) { done(lines) },
	}
	l.BatchAsync(commands, cbs[:len(commands)])
}

// HistoryPage is the scrollback lines [before-limit, before) by absolute index (0 is the oldest line the pane keeps), as the host keeps
// them, trimmed to 1 MiB; nil when the host could not say.
func HistoryPage(l *ControlLink, handle string, before, limit int) map[string]any {
	// The size of the scrollback and the lines of it are read in one command line, at one place in the stream: the lines are indexed from
	// the end, so output that scrolled between two reads would shift every index. The whole scrollback is read (tmux keeps at most its
	// history-limit lines).
	var modes *Modes
	var scroll []string
	got := make(chan bool, 1)
	l.BatchAsync([]string{fmt.Sprintf("display-message -p -t %s '%s'", handle, modeFormat), fmt.Sprintf("capture-pane -p -e -t %s -S - -E -1", handle)},
		[]func([]string){func(lines []string) { modes = parseModes(lines) }, func(lines []string) { scroll = lines; got <- lines != nil }})
	select {
	case ok := <-got:
		if !ok || modes == nil {
			return nil
		}
	case <-time.After(CommandTimeout):
		return nil
	}
	if modes.AltScreen {
		return map[string]any{"first": 0, "lines": []any{}, "more": false}
	}
	total := modes.HistorySize
	if total == 0 {
		scroll = nil
	} else if len(scroll) > total {
		scroll = scroll[len(scroll)-total:]
	}
	end := max(min(before, total), 0)
	start := max(end-limit, 0)
	if end <= start {
		return map[string]any{"first": start, "lines": []any{}, "more": start > 0}
	}
	if end > len(scroll) {
		end = len(scroll)
	}
	page := scroll[min(start, end):end]
	var out []string
	used := 0
	for i := len(page) - 1; i >= 0; i-- { // the newest of the page first: a page trimmed to size keeps what is nearest before
		ln := page[i] + "\x1b[0m"
		used += len(ln)
		if used > SnapshotMax {
			break
		}
		out = append(out, ln)
	}
	lines := make([]any, len(out))
	for i, ln := range out {
		lines[len(out)-1-i] = ln
	}
	first := end - len(out)
	return map[string]any{"first": first, "lines": lines, "more": first > 0}
}

// ---------------------------------------------------------------- clients and channels

type queued struct {
	kind   string // "output", "notice" or "snapshot"
	data   []byte
	method string
	params map[string]any
}

// BytesClient is one bytes attach: the queue of what it is owed, its counter, and the writer that turns the queue into notices.
type BytesClient struct {
	send  func(map[string]any) bool
	close func()

	mu        sync.Mutex
	cond      *sync.Cond
	queue     []queued
	unsent    int // decoded bytes of output in the queue
	Seq       int
	dead      bool
	channel   *BytesChannel
	live      bool // owed the output that comes next: set at the place in the stream the snapshot was taken
	resyncing bool
}

func NewBytesClient(send func(map[string]any) bool, closeConn func()) *BytesClient {
	c := &BytesClient{send: send, close: closeConn}
	c.cond = sync.NewCond(&c.mu)
	return c
}

func (c *BytesClient) start(ch *BytesChannel) {
	c.channel = ch
	go c.run()
}

func (c *BytesClient) end() {
	c.mu.Lock()
	c.dead = true
	c.mu.Unlock()
	c.cond.Broadcast()
}

// putOutput queues output; true when this client has fallen a megabyte behind and must resync.
func (c *BytesClient) putOutput(data []byte) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.dead || c.resyncing || !c.live {
		return false
	}
	if n := len(c.queue); n > 0 && c.queue[n-1].kind == "output" {
		c.queue[n-1].data = append(c.queue[n-1].data, data...)
	} else {
		c.queue = append(c.queue, queued{kind: "output", data: append([]byte(nil), data...)})
	}
	c.unsent += len(data)
	c.cond.Broadcast()
	return c.unsent >= SlowBytes
}

func (c *BytesClient) putNotice(method string, params map[string]any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.dead && c.live {
		c.queue = append(c.queue, queued{kind: "notice", method: method, params: params})
		c.cond.Broadcast()
	}
}

// resyncWith drops everything unsent and puts params (a snapshot) in its place; markers and holder changes stay, after it.
func (c *BytesClient) resyncWith(params map[string]any) {
	c.mu.Lock()
	kept := []queued{{kind: "snapshot", params: params}}
	for _, q := range c.queue {
		if q.kind == "notice" {
			kept = append(kept, q)
		}
	}
	c.queue, c.unsent, c.resyncing = kept, 0, false
	c.mu.Unlock()
	c.cond.Broadcast()
}

func (c *BytesClient) run() {
	var last time.Time
	for {
		c.mu.Lock()
		for len(c.queue) == 0 && !c.dead {
			c.cond.Wait()
		}
		if c.dead {
			c.mu.Unlock()
			return
		}
		item := c.queue[0]
		var msg map[string]any
		if item.kind == "output" {
			if wait := OutputEvery - time.Since(last); wait > 0 {
				c.mu.Unlock()
				time.Sleep(wait)
				continue
			}
			chunk, rest := item.data, []byte(nil)
			if len(chunk) > OutputMax {
				chunk, rest = chunk[:OutputMax], chunk[OutputMax:]
			}
			if len(rest) > 0 {
				c.queue[0].data = rest
			} else {
				c.queue = c.queue[1:]
			}
			c.unsent -= len(chunk)
			c.Seq++
			msg = map[string]any{"jsonrpc": "2.0", "method": "output", "params": map[string]any{"terminal": c.channel.Handle, "seq": c.Seq,
				"data": base64.StdEncoding.EncodeToString(chunk)}}
			last = time.Now()
		} else {
			c.queue = c.queue[1:]
			c.Seq++
			method := item.method
			if item.kind == "snapshot" {
				method = "snapshot"
			}
			params := map[string]any{"terminal": c.channel.Handle, "seq": c.Seq}
			for k, v := range item.params {
				params[k] = v
			}
			msg = map[string]any{"jsonrpc": "2.0", "method": method, "params": params}
		}
		c.mu.Unlock()
		if !c.send(msg) {
			c.end()
			c.channel.Detach(c)
			return
		}
	}
}

// BytesChannel is the bytes attaches of one pane: it watches the pane's output on the control link while anyone is attached.
type BytesChannel struct {
	Handle string
	hub    *BytesHub
	link   *ControlLink

	mu       sync.Mutex
	clients  []*BytesClient
	watching *Handler
}

func (ch *BytesChannel) onOutput(data []byte, ended bool) {
	ch.mu.Lock()
	cs := append([]*BytesClient(nil), ch.clients...)
	ch.mu.Unlock()
	if ended { // the control link ended: every client attaches again
		for _, c := range cs {
			c.close()
		}
		return
	}
	for _, c := range cs {
		if c.putOutput(data) {
			ch.resync(c, "slow_client")
		}
	}
}

func (ch *BytesChannel) resync(c *BytesClient, reason string) {
	c.mu.Lock()
	if c.resyncing {
		c.mu.Unlock()
		return
	}
	c.resyncing = true
	c.mu.Unlock()
	Capture(ch.link, ch.Handle, 0, func(s *Snapshot) {
		if s == nil {
			c.mu.Lock()
			c.resyncing = false
			c.mu.Unlock()
			return
		}
		p := s.encode()
		p["reason"] = reason
		c.resyncWith(p)
	}, nil)
}

// Attach is the snapshot for c, with the stream starting at the place the screen was taken: nil when the host could not say.
func (ch *BytesChannel) Attach(c *BytesClient, history int) *Snapshot {
	ch.mu.Lock()
	if ch.watching == nil {
		ch.watching = ch.link.Watch(ch.Handle, ch.onOutput)
	}
	ch.clients = append(ch.clients, c)
	c.channel = ch
	ch.mu.Unlock()
	got := make(chan *Snapshot, 1)
	Capture(ch.link, ch.Handle, history, func(s *Snapshot) { got <- s }, func() {
		c.mu.Lock() // on the reader, in order with the output: from the screen's place the client is owed what comes next
		c.live = true
		c.mu.Unlock()
	})
	select {
	case s := <-got:
		if s != nil {
			return s
		}
	case <-time.After(CommandTimeout):
	}
	ch.Detach(c)
	return nil
}

// Begin starts the client's writer once the reply is out.
func (ch *BytesChannel) Begin(c *BytesClient) { c.start(ch) }

func (ch *BytesChannel) Detach(c *BytesClient) {
	ch.mu.Lock()
	for i, x := range ch.clients {
		if x == c {
			ch.clients = append(ch.clients[:i:i], ch.clients[i+1:]...)
			break
		}
	}
	c.end()
	if len(ch.clients) == 0 && ch.watching != nil {
		ch.link.Unwatch(ch.Handle, ch.watching)
		ch.watching = nil
	}
	empty := len(ch.clients) == 0
	ch.mu.Unlock()
	if empty {
		ch.hub.reap(ch.Handle)
	}
}

func (ch *BytesChannel) notify(method string, params map[string]any) {
	ch.mu.Lock()
	cs := append([]*BytesClient(nil), ch.clients...)
	ch.mu.Unlock()
	for _, c := range cs {
		p := map[string]any{"format": "bytes"}
		for k, v := range params {
			p[k] = v
		}
		c.putNotice(method, p)
	}
}

// resized: the size changed (a holder's or the restore): every client is sent a snapshot, reason resize, and no resize notice.
func (ch *BytesChannel) resized() {
	ch.mu.Lock()
	cs := append([]*BytesClient(nil), ch.clients...)
	ch.mu.Unlock()
	for _, c := range cs {
		ch.resync(c, "resize")
	}
}

// BytesHub holds the bytes channels by pane.
type BytesHub struct {
	mu       sync.Mutex
	channels map[string]*BytesChannel
}

func NewBytesHub() *BytesHub { return &BytesHub{channels: map[string]*BytesChannel{}} }

func (h *BytesHub) Channel(handle string, link *ControlLink) *BytesChannel {
	h.mu.Lock()
	defer h.mu.Unlock()
	ch := h.channels[handle]
	if ch == nil {
		ch = &BytesChannel{Handle: handle, hub: h, link: link}
		h.channels[handle] = ch
	}
	return ch
}

func (h *BytesHub) openFor(handle string) *BytesChannel {
	h.mu.Lock()
	ch := h.channels[handle]
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

func (h *BytesHub) reap(handle string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if ch := h.channels[handle]; ch != nil {
		ch.mu.Lock()
		if len(ch.clients) == 0 {
			delete(h.channels, handle)
		}
		ch.mu.Unlock()
	}
}

// Notify sends a marker or holder notice to the bytes attaches of handle, in each one's counter.
func (h *BytesHub) Notify(handle, method string, params map[string]any) {
	if ch := h.openFor(handle); ch != nil {
		ch.notify(method, params)
	}
}

// Resized tells the bytes attaches of handle the size changed.
func (h *BytesHub) Resized(handle string) {
	if ch := h.openFor(handle); ch != nil {
		ch.resized()
	}
}
