package rpc

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os/user"
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
}

// Server holds what every connection shares.
type Server struct {
	Auth      authority.Authority
	Sha       string // the build's git sha, reported in hello
	Schema    int
	Transport string // "local" or "network"
	Log       func(format string, args ...any)
	methods   map[string]Method
	order     []string
	founder   string
}

func NewServer(auth authority.Authority, sha string, schema int, transport string, log func(string, ...any)) *Server {
	s := &Server{Auth: auth, Sha: sha, Schema: schema, Transport: transport, Log: log, methods: map[string]Method{}}
	if u, err := user.Current(); err == nil {
		s.founder = u.Username
	}
	s.Register("hello", Method{"anonymous", hello})
	s.Register("whoami", Method{"agent", whoami})
	return s
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
	wmu       sync.Mutex
}

// Serve answers calls on tr until it ends. pid is the peer process on the local socket, 0 elsewhere.
func (s *Server) Serve(tr Transport, pid int) {
	c := &Conn{srv: s, tr: tr, Pid: pid}
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
		if !c.one(raw) {
			return
		}
	}
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
	result, err := c.dispatch(msg)
	if err != nil {
		e, ok := err.(*Error)
		if !ok {
			c.srv.Log("  rpc: %v", err)
			e = &Error{RPCCode: Internal, Code: "internal", Message: "internal error"}
		}
		return c.send(map[string]any{"jsonrpc": "2.0", "id": rid, "error": e.wire()})
	}
	if _, has := obj["id"]; isObj && !has {
		return true // a notification is never answered
	}
	return c.send(map[string]any{"jsonrpc": "2.0", "id": rid, "result": result})
}

func (c *Conn) dispatch(msg any) (any, error) {
	if _, batch := msg.([]any); batch {
		return nil, BadRequestError("batches are not accepted", "", BadRequest)
	}
	obj, ok := msg.(map[string]any)
	if !ok || obj["jsonrpc"] != "2.0" {
		return nil, BadRequestError("a request is a JSON-RPC 2.0 object", "", BadRequest)
	}
	if id, has := obj["id"]; has {
		switch id.(type) {
		case json.Number, string:
		default:
			return nil, BadRequestError("id is an integer or a string", "id", BadRequest)
		}
	}
	name, isStr := obj["method"].(string)
	if !isStr {
		return nil, BadRequestError("method is a string", "method", BadRequest)
	}
	params := map[string]any{}
	if p, has := obj["params"]; has {
		pm, isMap := p.(map[string]any)
		if !isMap {
			return nil, BadRequestError("params go by name, as an object", "params", InvalidParams)
		}
		params = pm
	}
	if name != "hello" && !c.saidHello {
		return nil, BadRequestError("the first call is hello", "method", BadRequest)
	}
	m, found := c.srv.methods[name]
	if !found {
		return nil, BadRequestError("no such method: "+name, "method", NoSuchMethod)
	}
	if !c.Caller.Allows(m.Role) {
		return nil, Refused(fmt.Sprintf("%s is for a %s or above; this caller is %s", name, m.Role, c.Caller.Role), "role", "")
	}
	result, err := m.Handler(c, params)
	if err != nil {
		return nil, err
	}
	if name == "hello" {
		c.saidHello = true
	}
	return result, nil
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
	return out
}

func asInt(v any) (int64, bool) {
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
	protocol, ok := asInt(params["protocol"])
	if !ok {
		return nil, BadRequestError("hello needs protocol, an integer", "protocol", InvalidParams)
	}
	if protocol < MinClient {
		return nil, Refused(fmt.Sprintf("this daemon serves clients of protocol %d or newer; yours speaks %d", MinClient, protocol),
			"protocol", "hl service install")
	}
	key, _ := params["key"].(string)
	if tok, has := params["token"]; has && tok != nil && c.srv.Transport != "local" {
		return nil, Refused("device tokens are not available yet", "unsupported", "")
	}
	c.Caller = identify(c.srv.Auth, c.srv.Transport == "local", c.Pid, key, c.srv.founder)
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
