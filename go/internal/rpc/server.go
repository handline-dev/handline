package rpc

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"os/user"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/authority"
)

const (
	Protocol     = 1
	MinClient    = 1
	MaxLineBytes = 1 << 20
	// A message that has started must finish within RecvTimeout; a connection past its hello may wait
	// between calls up to IdleTimeout. Before hello it gets the short limit.
	RecvTimeout = 30 * time.Second
	IdleTimeout = time.Hour
)

// Transport carries whole messages: a line on the local socket, a text frame on the network listener.
// ReadMessage returns the next one, or io.EOF at a clean end. A message past MaxLineBytes is a *Error.
type Transport interface {
	ReadMessage(timeout time.Duration) ([]byte, error)
	WriteMessage([]byte) error
	// Pending says whether bytes of a message already wait, so the long idle limit applies only between messages.
	Pending() bool
}

// Method is one entry of the method table. Role is the lowest role that may call it.
type Method struct {
	Role    string
	Handler func(c *Conn, params map[string]any) (any, error)
	// Waits marks a method that may hold its call for a long time: it runs beside the connection's other calls
	// and stops when the client hangs up (Conn.Done).
	Waits bool
	// Write marks a method that changes something: it takes a request_id and runs once per id (runWrite).
	// OwnEvent says it writes its own event, so the generic rpc_write one is not added.
	Write, OwnEvent bool
	// Relays says, on a gateway, that this call is for the daemon behind it and not served here: it is asked with the
	// call's params, and only for a paired device's connection.
	Relays func(params map[string]any) bool
	// Unkept marks a write whose reply is never kept for a retry because it holds a secret that is given once
	// (a pairing code, a device token): the retry runs again and is judged on its own.
	Unkept bool
}

// Server holds what every connection shares.
type Server struct {
	Auth      authority.Authority
	Sha       string // the build's git sha, reported in hello
	Schema    int
	Transport string // "local" or "network"
	Log       func(format string, args ...any)
	DB        *sql.DB // the database the write methods keep their replies in
	Net       NetGate // the paired devices of a network server; nil on the local one
	// Relay is set on a gateway's network server: a paired device's call for a method this server does not have, and
	// the contract lets a member call, is sent to the daemon behind it.
	Relay *Relay
	// NoEvents keeps a write from leaving the generic event: a gateway writes nothing but its devices.
	NoEvents bool
	methods  map[string]Method
	order    []string
	founder  string
}

func NewServer(auth authority.Authority, sha string, schema int, transport string, log func(string, ...any)) *Server {
	s := &Server{Auth: auth, Sha: sha, Schema: schema, Transport: transport, Log: log, methods: map[string]Method{}}
	if u, err := user.Current(); err == nil {
		s.founder = u.Username
	}
	s.Register("hello", Method{Role: "anonymous", Handler: hello})
	s.Register("whoami", Method{Role: "agent", Handler: whoami})
	return s
}

// Restrict drops every method keep says no to: a gateway serves natively only what it is told to.
func (s *Server) Restrict(keep func(name string) bool) {
	var order []string
	for _, n := range s.order {
		if keep(n) {
			order = append(order, n)
		} else {
			delete(s.methods, n)
		}
	}
	s.order = order
}

// Register adds a method. Every method a later task adds is a line in the table.
func (s *Server) Register(name string, m Method) {
	if _, dup := s.methods[name]; !dup {
		s.order = append(s.order, name)
	}
	s.methods[name] = m
}

// Conn is one connection's state.
type Conn struct {
	srv       *Server
	tr        Transport
	Pid       int
	Caller    Caller
	saidHello bool
	// Remote is the peer's host on the network listener, "" elsewhere.
	Remote     string
	closeFn    func()
	device     string // the paired device this connection hello'd with
	deviceName string
	release    func()
	rmu        sync.Mutex
	rs         *relaySession
	wmu        sync.Mutex
	done       chan struct{} // closed when the client is gone
	waiting    sync.WaitGroup
	// afterReply runs once the reply of the call in hand is sent; onClose when the connection ends.
	afterReply, onClose []func()
	// callName and callRID name the write in hand while its handler runs (see Effect).
	callName, callRID string
}

// NetGate is what a network server knows of paired devices: a call from a connection that no device token
// opened is refused before any method runs.
type NetGate interface {
	// Know says whether token is a paired device's.
	Know(token string) bool
	// Login binds a connection to the device token belongs to; closeConn is what revoking the device calls.
	// release unbinds it when the connection ends.
	Login(token string, closeConn func()) (id, name string, release func(), ok bool)
	// Live says whether the device still has its token.
	Live(id string) bool
}

// Server is the server this connection belongs to.
func (c *Conn) Server() *Server { return c.srv }

// SendRaw writes one whole message, as the stream's notices are; false when the client is gone.
func (c *Conn) SendRaw(b []byte) bool {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	return c.tr.WriteMessage(b) == nil
}

// HangUp closes the connection from the daemon's side.
func (c *Conn) HangUp() {
	if cl, ok := c.tr.(interface{ Close() error }); ok {
		cl.Close()
	}
}

// OnClose runs f when the connection ends. Call it from a method's handler.
func (c *Conn) OnClose(f func()) { c.onClose = append(c.onClose, f) }

// AfterReply runs f once the reply to the call being handled is sent: for what must not reach the client first.
// Not for a method that Waits.
func (c *Conn) AfterReply(f func()) { c.afterReply = append(c.afterReply, f) }

// Done is closed once the client has hung up: a call that waits stops then.
func (c *Conn) Done() <-chan struct{} { return c.done }

// Serve answers calls on tr until it ends. pid is the peer process on the local socket, 0 elsewhere.
func (s *Server) Serve(tr Transport, pid int) { s.serve(tr, pid, "", nil) }

// ServeNet serves a connection of the network listener: remote is the peer's host, closeConn ends the connection.
func (s *Server) ServeNet(tr Transport, remote string, closeConn func()) {
	s.serve(tr, 0, remote, closeConn)
}

func (s *Server) serve(tr Transport, pid int, remote string, closeConn func()) {
	c := &Conn{srv: s, tr: tr, Pid: pid, Remote: remote, closeFn: closeConn, done: make(chan struct{})}
	defer func() {
		close(c.done)
		c.waiting.Wait()
		c.closeRelay()
		for _, f := range c.onClose {
			f()
		}
		if c.release != nil {
			c.release()
		}
	}()
	c.Caller = identify(s.Auth, s.Transport == "local", pid, "", s.founder)
	for {
		timeout := RecvTimeout
		if c.saidHello && !tr.Pending() {
			timeout = IdleTimeout
		}
		raw, err := tr.ReadMessage(timeout)
		if err != nil {
			if e, ok := err.(*Error); ok {
				c.send(map[string]any{"jsonrpc": "2.0", "id": nil, "error": e.wire()})
			}
			return // the rest of the stream cannot be framed, or the client is gone
		}
		if len(bytes.TrimSpace(raw)) == 0 {
			continue
		}
		if s.Net != nil {
			switch c.admit(raw) {
			case refuse:
				if !c.send(unpairedReply(raw)) {
					return
				}
				continue
			case drop:
				c.send(unpairedReply(raw))
				return
			}
		}
		if !c.one(raw) {
			return
		}
	}
}

type admission int

const (
	let admission = iota
	refuse
	drop
)

// admit is the network gate, before the message is even parsed into a call: a connection no device token opened
// may call device.redeem, or hello with a token that is paired; a paired one stays only while its device does.
func (c *Conn) admit(raw []byte) admission {
	if c.device != "" {
		if c.srv.Net.Live(c.device) {
			return let
		}
		return drop
	}
	if msg, err := decode(raw); err == nil {
		if obj, ok := msg.(map[string]any); ok && obj["jsonrpc"] == "2.0" {
			switch obj["method"] {
			case "device.redeem":
				return let
			case "hello":
				if p, ok := obj["params"].(map[string]any); ok {
					if tok, ok := p["token"].(string); ok && c.srv.Net.Know(tok) {
						return let
					}
				}
			}
		}
	}
	return refuse
}

// UnpairedError is the one refusal an unpaired network caller gets: the same words every time, so a reply
// says nothing about why.
func UnpairedError() *Error {
	return &Error{RPCCode: RefusedCode, Code: "refused", Message: "refused", Data: map[string]any{"gate": "unpaired", "reason": "not paired"}}
}

func unpairedReply(raw []byte) map[string]any {
	var rid any
	if msg, err := decode(raw); err == nil {
		if obj, ok := msg.(map[string]any); ok {
			switch v := obj["id"].(type) {
			case json.Number, string:
				rid = v
			}
		}
	}
	return map[string]any{"jsonrpc": "2.0", "id": rid, "error": UnpairedError().wire()}
}

func (c *Conn) send(obj map[string]any) bool {
	b, err := json.Marshal(obj)
	if err != nil {
		return false
	}
	c.wmu.Lock()
	defer c.wmu.Unlock()
	return c.tr.WriteMessage(b) == nil
}

func decode(raw []byte) (any, error) {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var v any
	if err := d.Decode(&v); err != nil {
		return nil, err
	}
	if d.More() {
		return nil, fmt.Errorf("extra data after the first JSON value")
	}
	return v, nil
}

// one answers a message; false when the connection is done.
func (c *Conn) one(raw []byte) bool {
	msg, err := decode(raw)
	if err != nil {
		return c.send(map[string]any{"jsonrpc": "2.0", "id": nil,
			"error": BadRequestError("not JSON: "+err.Error(), "", ParseError).wire()})
	}
	obj, isObj := msg.(map[string]any)
	if isObj {
		if _, verb := obj["verb"]; verb && obj["jsonrpc"] == nil {
			c.send(map[string]any{"ok": false, "said": "the legacy verbs are not served by this daemon yet"})
			return false
		}
	}
	var rid any
	if isObj {
		switch v := obj["id"].(type) {
		case json.Number, string:
			rid = v
		}
	}
	call, err := c.route(msg, raw)
	if err != nil {
		return c.fail(rid, err)
	}
	_, hasID := obj["id"]
	if call.relay != nil {
		if err := call.relay(rid, hasID); err != nil {
			return c.fail(rid, err)
		}
		return true
	}
	answer := func() bool {
		result, err := call.run()
		if err != nil {
			return c.fail(rid, err)
		}
		sent := true // a notification is never answered
		if hasID {
			sent = c.send(map[string]any{"jsonrpc": "2.0", "id": rid, "result": result})
		}
		if !call.waits { // a waiting call runs beside the loop on a Conn of its own
			for _, f := range c.afterReply {
				f()
			}
			c.afterReply = nil
		}
		return sent
	}
	if call.waits {
		c.waiting.Add(1)
		go func() {
			defer c.waiting.Done()
			answer()
		}()
		return true
	}
	return answer()
}

func (c *Conn) fail(rid any, err error) bool {
	e, ok := err.(*Error)
	if !ok {
		c.srv.Log("  rpc: %v", err)
		e = &Error{RPCCode: Internal, Code: "internal", Message: "internal error"}
	}
	return c.send(map[string]any{"jsonrpc": "2.0", "id": rid, "error": e.wire()})
}

// routed is a call that passed every check before its handler: run it now, or beside the connection's other calls.
type routed struct {
	waits bool
	run   func() (any, error)
	relay func(id any, hasID bool) error // a call for the daemon behind a gateway: nothing is answered here
}

func (c *Conn) route(msg any, raw []byte) (routed, error) {
	if _, batch := msg.([]any); batch {
		return routed{}, BadRequestError("batches are not accepted", "", BadRequest)
	}
	obj, ok := msg.(map[string]any)
	if !ok || obj["jsonrpc"] != "2.0" {
		return routed{}, BadRequestError("a request is a JSON-RPC 2.0 object", "", BadRequest)
	}
	if id, has := obj["id"]; has {
		switch id.(type) {
		case json.Number, string:
		default:
			return routed{}, BadRequestError("id is an integer or a string", "id", BadRequest)
		}
	}
	name, isStr := obj["method"].(string)
	if !isStr {
		return routed{}, BadRequestError("method is a string", "method", BadRequest)
	}
	params := map[string]any{}
	if p, has := obj["params"]; has {
		pm, isMap := p.(map[string]any)
		if !isMap {
			return routed{}, BadRequestError("params go by name, as an object", "params", InvalidParams)
		}
		params = pm
	}
	if name != "hello" && name != "device.redeem" && !c.saidHello {
		return routed{}, BadRequestError("the first call is hello", "method", BadRequest)
	}
	m, found := c.srv.methods[name]
	if found && m.Relays != nil && c.srv.Relay != nil && c.device != "" && m.Relays(params) {
		return c.routeRelay(name, raw)
	}
	if !found {
		if c.srv.Relay != nil && c.device != "" {
			return c.routeRelay(name, raw)
		}
		return routed{}, BadRequestError("no such method: "+name, "method", NoSuchMethod)
	}
	if !c.Caller.Allows(m.Role) {
		return routed{}, Refused(fmt.Sprintf("%s is for a %s or above; this caller is %s", name, m.Role, c.Caller.Role), "role", "")
	}
	// A call that waits runs on its own goroutine while a later hello may replace c.Caller: it gets the caller as
	// it was when routed, on a Conn of its own that shares only what stays fixed.
	at := c
	if m.Waits {
		at = &Conn{srv: c.srv, tr: c.tr, Pid: c.Pid, Remote: c.Remote, Caller: c.Caller, saidHello: c.saidHello, done: c.done}
	}
	return routed{waits: m.Waits, run: func() (any, error) {
		var result any
		var err error
		if m.Write {
			result, err = at.runWrite(name, m, params)
		} else {
			result, err = m.Handler(at, params)
		}
		if err != nil {
			return nil, err
		}
		if name == "hello" {
			c.saidHello = true
		}
		return result, nil
	}}, nil
}

// routeRelay checks a call this gateway does not serve against the contract, here, before anything is sent on: a
// method the contract lacks is unknown, one above a member's reach is refused.
func (c *Conn) routeRelay(name string, raw []byte) (routed, error) {
	known, allowed := memberMay(name)
	if !known {
		return routed{}, BadRequestError("no such method: "+name, "method", NoSuchMethod)
	}
	if !allowed {
		return routed{}, Refused(fmt.Sprintf("%s is not for a paired device", name), "role", "")
	}
	return routed{relay: func(id any, hasID bool) error { return c.relay(raw, id, hasID) }}, nil
}

// RequireSelf is for the methods that act as the caller's own session: an agent that is not the caller is refused.
func (c *Conn) RequireSelf(agent string) error {
	if c.Caller.Role == "agent" && agent != c.Caller.Info["name"] {
		return Refused(fmt.Sprintf("%q is not this session's agent", agent), "not_self", "")
	}
	return nil
}

func (c *Conn) methodList() []map[string]any {
	out := make([]map[string]any, 0, len(c.srv.order))
	for _, n := range c.srv.order {
		m := c.srv.methods[n]
		out = append(out, map[string]any{"name": n, "since": 1, "role": m.Role, "allowed": c.Caller.Allows(m.Role)})
	}
	if c.srv.Relay != nil {
		var rest []string
		for n := range contract {
			if _, own := c.srv.methods[n]; !own {
				rest = append(rest, n)
			}
		}
		sort.Strings(rest)
		for _, n := range rest {
			_, ok := memberMay(n)
			out = append(out, map[string]any{"name": n, "since": 1, "role": contract[n].Role,
				"allowed": ok && c.device != ""})
		}
	}
	return out
}

// AsInt is a JSON number that is a whole one.
func AsInt(v any) (int64, bool) {
	n, ok := v.(json.Number)
	if !ok || strings.ContainsAny(string(n), ".eE") {
		return 0, false
	}
	i, err := n.Int64()
	return i, err == nil
}

func hello(c *Conn, params map[string]any) (any, error) {
	if client, ok := params["client"].(string); !ok || client == "" {
		return nil, BadRequestError("hello needs client, the name/version of the caller", "client", InvalidParams)
	}
	protocol, ok := AsInt(params["protocol"])
	if !ok {
		return nil, BadRequestError("hello needs protocol, an integer", "protocol", InvalidParams)
	}
	if protocol < MinClient {
		return nil, Refused(fmt.Sprintf("this daemon serves clients of protocol %d or newer; yours speaks %d", MinClient, protocol),
			"protocol", "hl service install")
	}
	key, _ := params["key"].(string)
	if c.srv.Net != nil {
		tok, _ := params["token"].(string)
		id, name, release, ok := c.srv.Net.Login(tok, c.closeFn)
		if !ok {
			return nil, UnpairedError()
		}
		if c.release != nil {
			c.release()
		}
		c.device, c.deviceName, c.release = id, name, release
		c.Caller = Caller{"member", map[string]any{"kind": "device", "device": id, "name": name}}
	} else {
		c.Caller = identify(c.srv.Auth, c.srv.Transport == "local", c.Pid, key, c.srv.founder)
	}
	return map[string]any{
		"protocol": Protocol, "min_client": MinClient, "daemon": c.srv.Sha, "schema": c.srv.Schema,
		"role": c.Caller.Role, "caller": c.Caller.Info,
		"capabilities": map[string]any{"chat": false, "review": false, "input_ask": false, "terminal_format": []any{}},
		"transport":    c.srv.Transport,
		"methods":      c.methodList(),
	}, nil
}

func whoami(c *Conn, _ map[string]any) (any, error) {
	return map[string]any{"caller": c.Caller.Info, "role": c.Caller.Role, "methods": c.methodList()}, nil
}
