package termchan

import (
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"regexp"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// terminal.attach, detach, history, resize and input; input.take, release and ask (handline/daemon/rpc_terminal.py, rpc_input.py,
// rpc_gates.py). They serve the IDE and the web UI, which no longer read terminals themselves; no hl command serves them.
//
// attach replies with the snapshot (the frame and one page of history) and then, once that reply is sent, the connection is pushed
// frame, resize, marker and holder notices until it detaches or hangs up. Many connections watch one terminal with one poller, which
// stops with the last of them. resize is the input holder's alone, by the holder gate (the daemon holding is refused too: nobody holds
// it to resize it), and where the host cannot resize it says so (gate unsupported); the size before it is given back when the holder
// leaves. A terminal account.login opened belongs to no agent: it can be watched, has no holder, and is not resized.

const (
	DefaultHistory  = 100
	MaxHistory      = 2000
	MaxCols         = 500
	MaxRows         = 200
	MaxInput        = 16384
	typingElsewhere = "someone else is typing at this agent"
)

var base64RE = regexp.MustCompile(`^[A-Za-z0-9+/]*={0,2}$`)

// Env is what the terminal methods share.
type Env struct {
	DB      *sql.DB
	Home    string
	Backend func() term.Backend
	Now     func() time.Time

	Hub   *Hub
	Bytes *BytesHub

	mu    sync.Mutex
	link  *ControlLink
	login map[string]bool // handles of the terminals account.login opened
	conns map[*rpc.Conn]*attaches
}

// New is the terminal channel over db, reading the terminals of backend().
func New(db *sql.DB, home string, backend func() term.Backend) *Env {
	e := &Env{DB: db, Home: home, Backend: backend, login: map[string]bool{}, conns: map[*rpc.Conn]*attaches{}, Bytes: NewBytesHub()}
	e.Hub = NewHub(e.host)
	return e
}

func (e *Env) host() Host { return Host{e.Backend()} }

func (e *Env) now() time.Time {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now()
}

// AddLoginTerminal makes handle a terminal that can be watched though no agent has it.
func (e *Env) AddLoginTerminal(handle string) {
	e.mu.Lock()
	e.login[handle] = true
	e.mu.Unlock()
}

// SetLink gives the bytes format a control link of its own (a test's); by default it is tmux's.
func (e *Env) SetLink(l *ControlLink) {
	e.mu.Lock()
	e.link = l
	e.mu.Unlock()
}

func (e *Env) controlLink() *ControlLink {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.link == nil {
		argv := []string{"tmux", "-C"}
		if s, ok := e.Backend().(term.ControlSpawner); ok {
			argv = s.ControlCommand()
		}
		e.link = NewControlLink(SpawnCommand(argv), true)
	}
	return e.link
}

// Register adds the terminal and input methods, and makes this the channel that is told when a holder changes.
func Register(s *rpc.Server, e *Env) {
	inputhold.Changed = e.holdersChanged
	s.Capabilities = capabilities
	s.Register("terminal.attach", rpc.Method{Role: "member", Handler: e.attach})
	s.Register("terminal.detach", rpc.Method{Role: "member", Handler: e.detach})
	s.Register("terminal.history", rpc.Method{Role: "member", Handler: e.history})
	s.Register("terminal.resize", rpc.Method{Role: "member", Write: true, Handler: e.resize})
	s.Register("terminal.input", rpc.Method{Role: "member", Write: true, Handler: e.input})
	s.Register("input.take", rpc.Method{Role: "member", Write: true, Handler: e.take})
	s.Register("input.release", rpc.Method{Role: "member", Write: true, Handler: e.release})
}

// capabilities is the formats the terminal channel serves, in all and by host (x-terminal-bytes): tmux has text, bytes and styled,
// Orca text.
func capabilities() map[string]any {
	return map[string]any{
		"terminal_format": []any{"text", "bytes", "styled"},
		"terminal_hosts":  map[string]any{"tmux": []any{"text", "bytes", "styled"}, "orca": []any{"text"}},
	}
}

func bad(msg, field string) error { return rpc.BadRequestError(msg, field, rpc.InvalidParams) }

func notFound(msg, what, name string) error {
	err := rpc.NotFoundError(msg)
	err.Data = map[string]any{"what": what, "name": name}
	return err
}

func intParam(params map[string]any, key string, lo, hi int64) (int, error) {
	v, ok := rpc.AsInt(params[key])
	if !ok || v < lo || v > hi {
		return 0, bad(fmt.Sprintf("%s is an integer from %d to %d", key, lo, hi), key)
	}
	return int(v), nil
}

func formatParam(params map[string]any) (string, error) {
	v, present := params["format"]
	if !present {
		return "text", nil
	}
	if f, _ := v.(string); f == "text" || f == "bytes" || f == "styled" {
		return f, nil
	}
	return "", bad("format is text, bytes or styled", "format")
}

func (e *Env) serves(format string) error {
	h := e.host()
	switch {
	case format == "bytes" && !h.ServesBytes():
		return rpc.Refused(fmt.Sprintf("the terminal host (%s) does not serve the bytes format: ask for text", h.Backend.Name()), "unsupported", "")
	case format == "styled" && !h.ServesStyled():
		return rpc.Refused(fmt.Sprintf("the terminal host (%s) does not serve the styled format: ask for text", h.Backend.Name()), "unsupported", "")
	}
	return nil
}

func (e *Env) terminalOf(agent string) (string, error) {
	var t sql.NullString
	err := e.DB.QueryRow("SELECT terminal FROM agent_state WHERE agent = ?", agent).Scan(&t)
	if err != nil && err != sql.ErrNoRows {
		return "", err
	}
	return t.String, nil
}

// handleOf is (terminal handle, the agent that has it, "" for a login terminal) for agent or terminal, never both; not_found for one
// nobody has.
func (e *Env) handleOf(params map[string]any) (handle, agent string, err error) {
	a, hasAgent := params["agent"]
	t, hasTerminal := params["terminal"]
	if a == nil {
		hasAgent = false
	}
	if t == nil {
		hasTerminal = false
	}
	if hasAgent == hasTerminal {
		return "", "", bad("give agent or terminal, not both", "agent")
	}
	if hasAgent {
		name, _ := a.(string)
		if name == "" {
			return "", "", bad("agent is a name", "agent")
		}
		if !has(paths.ListAgents(e.Home), name) {
			return "", "", notFound("no such agent: "+name, "agent", name)
		}
		h, err := e.terminalOf(name)
		if err != nil {
			return "", "", err
		}
		if h == "" {
			return "", "", notFound(name+" has no terminal", "terminal", name)
		}
		return h, name, nil
	}
	handle, _ = t.(string)
	if handle == "" {
		return "", "", bad("terminal is a handle", "terminal")
	}
	for _, name := range paths.ListAgents(e.Home) {
		if h, err := e.terminalOf(name); err != nil {
			return "", "", err
		} else if h == handle {
			return handle, name, nil
		}
	}
	e.mu.Lock()
	login := e.login[handle]
	e.mu.Unlock()
	if login {
		return handle, "", nil
	}
	return "", "", notFound("no terminal "+handle, "terminal", handle)
}

func has(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// attaches is what one connection watches: one attach a terminal and format, so a second attach replaces the first (its client
// leaves the channel at once, and is sent nothing after this reply). The other format is independent, and closing the connection ends
// them all.
type attaches struct {
	mu sync.Mutex
	m  map[[2]string]func()
}

func (e *Env) hold(c *rpc.Conn, handle, format string, leave func()) {
	e.mu.Lock()
	a := e.conns[c]
	if a == nil {
		a = &attaches{m: map[[2]string]func(){}}
		e.conns[c] = a
		c.OnClose(func() {
			e.mu.Lock()
			delete(e.conns, c)
			e.mu.Unlock()
			a.mu.Lock()
			all := a.m
			a.m = map[[2]string]func(){}
			a.mu.Unlock()
			for _, f := range all {
				f()
			}
		})
	}
	e.mu.Unlock()
	a.mu.Lock()
	old := a.m[[2]string{handle, format}]
	a.m[[2]string{handle, format}] = leave
	a.mu.Unlock()
	if old != nil {
		old()
	}
}

func sender(c *rpc.Conn) func(map[string]any) bool {
	return func(m map[string]any) bool {
		b, err := json.Marshal(m)
		return err == nil && c.SendRaw(b)
	}
}

func (e *Env) holders(agent string) ([]map[string]any, error) {
	if agent == "" {
		return []map[string]any{}, nil
	}
	return inputhold.View(e.DB, e.Home, agent, e.now())
}

func daemonHolder() map[string]any {
	return map[string]any{"kind": "daemon", "name": "daemon", "since": nil}
}

func (e *Env) attach(c *rpc.Conn, params map[string]any) (any, error) {
	history := DefaultHistory
	if _, ok := params["history"]; ok {
		var err error
		if history, err = intParam(params, "history", 0, MaxHistory); err != nil {
			return nil, err
		}
	}
	format, err := formatParam(params)
	if err != nil {
		return nil, err
	}
	handle, agent, err := e.handleOf(params)
	if err != nil {
		return nil, err
	}
	holders, err := e.holders(agent)
	if err != nil {
		return nil, err
	}
	if format == "bytes" {
		return e.attachBytes(c, handle, history, holders)
	}
	if format == "styled" {
		if err := e.serves("styled"); err != nil {
			return nil, err
		}
	}
	client := NewClient(sender(c), c.HangUp)
	ch := e.Hub.Attach(handle, client, format)
	c.AfterReply(func() { ch.Begin(client) })
	e.hold(c, handle, format, func() { ch.Detach(client) })
	if len(holders) == 0 {
		holders = []map[string]any{daemonHolder()}
	}
	return ch.Snapshot(history, holders), nil
}

func (e *Env) attachBytes(c *rpc.Conn, handle string, history int, holders []map[string]any) (any, error) {
	if err := e.serves("bytes"); err != nil {
		return nil, err
	}
	link := e.controlLink()
	if !link.Start() {
		return nil, rpc.Refused("the terminal host could not be reached", "state", "")
	}
	client := NewBytesClient(sender(c), c.HangUp)
	ch := e.Bytes.Channel(handle, link)
	snap := ch.Attach(client, history)
	if snap == nil {
		return nil, rpc.Refused("the terminal's screen could not be read", "state", "")
	}
	c.AfterReply(func() { ch.Begin(client) })
	e.hold(c, handle, "bytes", func() { ch.Detach(client) })
	holder := daemonHolder()
	if len(holders) > 0 {
		holder = holders[0]
	}
	out := snap.encode()
	out["terminal"], out["format"], out["seq"] = handle, "bytes", client.Seq
	out["history"], out["holder"], out["holders"] = snap.history(), holder, holders
	return out, nil
}

func (e *Env) detach(c *rpc.Conn, params map[string]any) (any, error) {
	handle, _ := params["terminal"].(string)
	if handle == "" {
		return nil, bad("terminal is a handle", "terminal")
	}
	format := ""
	if v, ok := params["format"]; ok && v != nil {
		if format, _ = v.(string); format != "text" && format != "bytes" && format != "styled" {
			return nil, bad("format is text, bytes or styled", "format")
		}
	}
	if _, _, err := e.handleOf(map[string]any{"terminal": handle}); err != nil { // not_found for a handle that does not exist; nothing to end is not an error
		return nil, err
	}
	e.mu.Lock()
	a := e.conns[c]
	e.mu.Unlock()
	if a != nil {
		a.mu.Lock()
		var leave []func()
		for k, f := range a.m {
			if k[0] == handle && (format == "" || format == k[1]) {
				leave = append(leave, f)
				delete(a.m, k)
			}
		}
		a.mu.Unlock()
		for _, f := range leave {
			f()
		}
	}
	return map[string]any{}, nil
}

func (e *Env) history(_ *rpc.Conn, params map[string]any) (any, error) {
	before, err := intParam(params, "before", 0, 1<<40)
	if err != nil {
		return nil, err
	}
	limit, err := intParam(params, "limit", 1, MaxHistory)
	if err != nil {
		return nil, err
	}
	format, err := formatParam(params)
	if err != nil {
		return nil, err
	}
	handle, _, err := e.handleOf(map[string]any{"terminal": params["terminal"]})
	if err != nil {
		return nil, err
	}
	if format == "bytes" {
		if err := e.serves("bytes"); err != nil {
			return nil, err
		}
		link := e.controlLink()
		if !link.Start() {
			return nil, rpc.Refused("the terminal host could not be reached", "state", "")
		}
		page := HistoryPage(link, handle, before, limit)
		if page == nil {
			return nil, rpc.Refused("the terminal's scrollback could not be read", "state", "")
		}
		return page, nil
	}
	if format == "styled" {
		if err := e.serves("styled"); err != nil {
			return nil, err
		}
	}
	if ch := e.Hub.OpenFor(handle, format); ch != nil { // scrollback is kept while someone watches: none for a terminal nobody does
		return ch.HistoryPage(before, limit), nil
	}
	return map[string]any{"first": 0, "lines": []any{}, "more": false}, nil
}

func callerName(c *rpc.Conn) string {
	n, _ := c.Caller.ActorInfo()["name"].(string)
	return n
}

func (e *Env) resize(c *rpc.Conn, params map[string]any) (any, error) {
	cols, err := intParam(params, "cols", 10, MaxCols)
	if err != nil {
		return nil, err
	}
	rows, err := intParam(params, "rows", 3, MaxRows)
	if err != nil {
		return nil, err
	}
	handle, agent, err := e.handleOf(map[string]any{"terminal": params["terminal"]})
	if err != nil {
		return nil, err
	}
	if agent == "" {
		return nil, rpc.Refused("a login terminal has no input holder to resize it", "input_holder", "")
	}
	me := callerName(c)
	holder, err := inputhold.Holder(e.DB, e.Home, agent, e.now())
	if err != nil {
		return nil, err
	}
	if holder == nil || holder.Name != me {
		who := "; the daemon does"
		if holder != nil {
			who = "; " + holder.Name + " does"
		}
		return nil, rpc.Refused("only whoever holds the terminal's input may resize it"+who, "input_holder", "")
	}
	h := e.host()
	bc, br, hadSize := h.Size(handle)
	if ok, why := h.Resize(handle, cols, rows); !ok {
		return nil, rpc.Refused(fmt.Sprintf("%s's terminal host cannot resize it: %s", agent, why), "unsupported", "")
	}
	if hadSize {
		e.Hub.Restore(handle, bc, br)
	}
	for _, ch := range e.Hub.OpenAll(handle) {
		ch.setSize(cols, rows)
		ch.Notify("resize", map[string]any{"cols": cols, "rows": rows})
	}
	e.Bytes.Resized(handle) // a bytes attach is sent a snapshot, reason resize, and no resize notice
	if err := inputhold.Touch(e.DB, agent, me, e.now()); err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": fmt.Sprintf("%s: terminal is %d by %d", agent, cols, rows)}, nil
}

// input is keystrokes and pasted bytes, from the input holder alone: refused while the daemon holds the input, and for a login
// terminal, which has none. Recorded as an event of the agent (kind type) by who sent it, with the byte count and never the bytes.
func (e *Env) input(c *rpc.Conn, params map[string]any) (any, error) {
	data, ok := params["data"].(string)
	if !ok || !base64RE.MatchString(data) {
		return nil, bad("data is base64", "data")
	}
	raw, err := base64.StdEncoding.DecodeString(data)
	if err != nil {
		return nil, bad("data is base64", "data")
	}
	if len(raw) < 1 || len(raw) > MaxInput {
		return nil, bad(fmt.Sprintf("data is 1 to %d bytes", MaxInput), "data")
	}
	_, agent, err := e.handleOf(map[string]any{"terminal": params["terminal"]})
	if err != nil {
		return nil, err
	}
	handle, _ := params["terminal"].(string)
	if agent == "" {
		return nil, rpc.Refused("a login terminal has no input holder to take its keystrokes", "input_holder", "")
	}
	if lock.TerminalBusy(e.Home, agent) {
		return nil, rpc.Refused(agent+": "+typingElsewhere, "terminal_lock", "")
	}
	me := callerName(c)
	holder, err := inputhold.Holder(e.DB, e.Home, agent, e.now())
	if err != nil {
		return nil, err
	}
	if holder == nil {
		return nil, rpc.Refused(agent+"'s input is held by the daemon: take it first", "input_holder", "")
	}
	if holder.Name != me {
		return nil, rpc.Refused(inputhold.HeldWhy(agent, holder.Name), "input_holder", "")
	}
	h := e.host()
	if !h.Backend.Available() || !h.ServesBytes() {
		return nil, rpc.Refused(fmt.Sprintf("the terminal host (%s) cannot send raw bytes", h.Backend.Name()), "unsupported", "")
	}
	hold := lock.TakeTerminal(e.Home, agent)
	if hold == nil {
		return nil, rpc.Refused(agent+": typing is going on elsewhere", "terminal_lock", "")
	}
	sent, why := h.SendBytes(handle, raw)
	hold.Release()
	if !sent {
		return nil, rpc.Refused(fmt.Sprintf("%s: nothing sent - %s", agent, why), "state", "")
	}
	result, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if _, err := db.AddEvent(tx, agent, "type", db.KV{K: "actor", V: c.Caller.ActorInfo()}, db.KV{K: "bytes", V: len(raw)}); err != nil {
			return nil, err
		}
		return map[string]any{"accepted": true, "said": fmt.Sprintf("%s: %d bytes sent", agent, len(raw))}, nil
	})
	if err != nil {
		return nil, err
	}
	if err := inputhold.Touch(e.DB, agent, me, e.now()); err != nil {
		return nil, err
	}
	return result, nil
}

func (e *Env) inputAgent(params map[string]any) (string, error) {
	name, _ := params["agent"].(string)
	if name == "" {
		return "", bad("agent is a name", "agent")
	}
	if !has(paths.ListAgents(e.Home), name) {
		return "", notFound("no such agent: "+name, "agent", name)
	}
	return name, nil
}

// holderKind is the spec's holder kind of the caller: a person, or a device.
func holderKind(c *rpc.Conn) string {
	if c.Caller.Info["kind"] == "device" {
		return "device"
	}
	return "person"
}

// holderGate refuses a call while a person or device other than the caller holds the input. The daemon holding it is a short hold, not
// a refusal.
func (e *Env) holderGate(c *rpc.Conn, agent string) error {
	row, err := inputhold.Holder(e.DB, e.Home, agent, e.now())
	if err != nil {
		return err
	}
	if row != nil && row.Name != callerName(c) {
		return rpc.Refused(inputhold.HeldWhy(agent, row.Name), "input_holder", "")
	}
	return nil
}

func (e *Env) take(c *rpc.Conn, params map[string]any) (any, error) {
	agent, err := e.inputAgent(params)
	if err != nil {
		return nil, err
	}
	if err := e.holderGate(c, agent); err != nil {
		return nil, err
	}
	won, err := inputhold.Take(e.DB, e.Home, agent, holderKind(c), callerName(c), e.now())
	if err != nil {
		return nil, err
	}
	if !won { // another took it first
		if err := e.holderGate(c, agent); err != nil {
			return nil, err
		}
		return nil, rpc.Refused(agent+"'s terminal was taken by someone else", "input_holder", "")
	}
	holders, err := inputhold.View(e.DB, e.Home, agent, e.now())
	if err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": you hold its input", "holder": holders[0]}, nil
}

func (e *Env) release(c *rpc.Conn, params map[string]any) (any, error) {
	agent, err := e.inputAgent(params)
	if err != nil {
		return nil, err
	}
	row, err := inputhold.Holder(e.DB, e.Home, agent, e.now())
	if err != nil {
		return nil, err
	}
	if row == nil {
		return map[string]any{"accepted": true, "said": agent + ": the daemon already holds its input"}, nil
	}
	if err := e.holderGate(c, agent); err != nil { // only its holder gives it back
		return nil, err
	}
	if err := inputhold.Release(e.DB, e.Home, agent, e.now()); err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": its input is the daemon's again"}, nil
}

// holdersChanged sends holder to every client of agent's terminal; a size taken for a holder that left is given back. This runs
// wherever the hold is found to have changed, an idle expiry noticed by a gate check included, so a host resize can run from a read path.
func (e *Env) holdersChanged(agent string, holders []map[string]any) {
	handle, err := e.terminalOf(agent)
	if err != nil || handle == "" {
		return
	}
	open := e.Hub.OpenAll(handle)
	hs := make([]any, len(holders))
	for i, h := range holders {
		hs[i] = h
	}
	for _, ch := range open {
		ch.Notify("holder", map[string]any{"holders": hs, "format": ch.Fmt})
	}
	e.Bytes.Notify(handle, "holder", map[string]any{"holders": hs})
	if holders[0]["kind"] != "daemon" {
		return
	}
	if size, ok := e.Hub.takeRestore(handle); ok {
		if done, _ := e.host().Resize(handle, size[0], size[1]); done {
			for _, ch := range open {
				ch.setSize(size[0], size[1])
				ch.Notify("resize", map[string]any{"cols": size[0], "rows": size[1]})
			}
			e.Bytes.Resized(handle)
		}
	}
}

// Marker tells whoever watches agent's terminal that the daemon typed (daemon_typed) or a control call happened (control).
func (e *Env) Marker(agent, kind string, actor map[string]any, text string) {
	handle, err := e.terminalOf(agent)
	if err != nil || handle == "" {
		return
	}
	at := time.Unix(e.now().Unix(), 0).UTC().Format("2006-01-02T15:04:05Z")
	for _, ch := range e.Hub.OpenAll(handle) {
		p := map[string]any{"at": at, "kind": kind, "actor": actor, "format": ch.Fmt}
		if text != "" {
			p["text"] = text
		}
		ch.Notify("marker", p)
	}
	p := map[string]any{"at": at, "kind": kind, "actor": actor}
	if text != "" {
		p["text"] = text
	}
	e.Bytes.Notify(handle, "marker", p)
}
