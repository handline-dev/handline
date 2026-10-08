package rpc

import (
	"bufio"
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"net"
	"sort"
	"sync"
	"time"
)

// contract.json is the method table of contract/protocol.json (tools/go_contract.py writes it): the lowest role of
// each method, and the only roles that may call it when the contract lists them.
//
//go:embed contract.json
var contractJSON []byte

type contractMethod struct {
	Role    string   `json:"role"`
	Callers []string `json:"callers"`
}

var contract = func() map[string]contractMethod {
	m := map[string]contractMethod{}
	if err := json.Unmarshal(contractJSON, &m); err != nil {
		panic("rpc/contract.json: " + err.Error())
	}
	return m
}()

// memberMay says whether the contract lets a paired device call name: it is a method, at member or below, and no
// list of callers leaves members out.
func memberMay(name string) (known, allowed bool) {
	m, known := contract[name]
	if !known {
		return false, false
	}
	if roleIndex("member") < roleIndex(m.Role) {
		return true, false
	}
	if m.Callers != nil {
		for _, r := range m.Callers {
			if r == "member" {
				return true, true
			}
		}
		return true, false
	}
	return true, true
}

// Relay is where a gateway sends the calls it does not serve itself: the Python daemon's relay socket.
type Relay struct {
	// Dial connects to it; a call fails at once when it cannot.
	Dial func() (net.Conn, error)
	// Capabilities, when set, is what hello asks for the terminal formats the daemon behind serves.
	Capabilities func(device, name string) map[string]any
}

const (
	relayOpenWait  = 5 * time.Second
	relayWriteWait = 5 * time.Second
	relayRetryMS   = 2000
)

// TerminalCapabilities asks the Python daemon, over a relay connection of its own for the device, which terminal formats
// it serves (hello.capabilities.terminal_format and terminal_hosts): what the gateway says in its hello for the calls it
// relays. Nil when the relay does not answer.
func (r *Relay) TerminalCapabilities(device, name string) map[string]any {
	up, err := r.Dial()
	if err != nil {
		return nil
	}
	defer up.Close()
	up.SetDeadline(time.Now().Add(relayOpenWait))
	open, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 0, "method": "relay.open",
		"params": map[string]any{"device_id": device, "name": name, "role": "member"}})
	hello, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "hello",
		"params": map[string]any{"client": "gateway", "protocol": MinClient}})
	br := bufio.NewReader(up)
	if _, err := up.Write(append(open, '\n')); err != nil {
		return nil
	}
	if _, err := br.ReadBytes('\n'); err != nil { // the open's reply
		return nil
	}
	if _, err := up.Write(append(hello, '\n')); err != nil {
		return nil
	}
	for {
		line, err := br.ReadBytes('\n')
		if err != nil {
			return nil
		}
		var m struct {
			ID     any `json:"id"`
			Result struct {
				Capabilities map[string]any `json:"capabilities"`
			} `json:"result"`
		}
		if json.Unmarshal(line, &m) == nil && m.ID == float64(1) {
			caps := map[string]any{}
			for _, k := range []string{"terminal_format", "terminal_hosts"} {
				if v, ok := m.Result.Capabilities[k]; ok {
					caps[k] = v
				}
			}
			return caps
		}
	}
}

func relayDown() *Error {
	return &Error{RPCCode: -32004, Code: "daemon_busy", Message: "the daemon behind this one is not answering",
		Data: map[string]any{"retry_after_ms": relayRetryMS}}
}

// relaySession is a device connection's line to the relay: one connection of its own, opened by the device's first
// relayed call. Everything the relay sends goes back to the device as it is, ids and notifications alike; closing it
// is how the relay learns the device is gone or was revoked.
type relaySession struct {
	c       *Conn
	up      net.Conn
	wmu     sync.Mutex
	mu      sync.Mutex
	pending map[string]struct{} // ids of calls sent and not yet answered
	closed  bool
}

func idKey(id any) string {
	b, _ := json.Marshal(id)
	return string(b)
}

// relay sends one validated call to the relay on the device's session, opening it first.
func (c *Conn) relay(raw []byte, id any, hasID bool) error {
	rs, err := c.relaySession()
	if err != nil {
		return err
	}
	rs.mu.Lock()
	if rs.closed {
		rs.mu.Unlock()
		return relayDown()
	}
	if hasID {
		rs.pending[idKey(id)] = struct{}{}
	}
	rs.mu.Unlock()
	rs.wmu.Lock()
	rs.up.SetWriteDeadline(time.Now().Add(relayWriteWait))
	_, err = rs.up.Write(append(bytes.TrimSpace(raw), '\n'))
	rs.wmu.Unlock()
	if err != nil {
		rs.up.Close() // the reader sees it and answers every pending call, this one included
		if hasID {
			return nil
		}
		return relayDown()
	}
	return nil
}

func (c *Conn) relaySession() (*relaySession, error) {
	c.rmu.Lock()
	defer c.rmu.Unlock()
	if c.rs != nil {
		return c.rs, nil
	}
	up, err := c.srv.Relay.Dial()
	if err != nil {
		c.srv.Log("  relay: %v", err)
		return nil, relayDown()
	}
	open, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 0, "method": "relay.open",
		"params": map[string]any{"device_id": c.device, "name": c.deviceName, "role": "member"}})
	up.SetDeadline(time.Now().Add(relayOpenWait))
	br := bufio.NewReaderSize(up, 64*1024)
	if _, err := up.Write(append(open, '\n')); err == nil {
		var line []byte
		if line, err = br.ReadBytes('\n'); err == nil {
			var r struct {
				Result map[string]any `json:"result"`
				Error  any            `json:"error"`
			}
			if json.Unmarshal(line, &r) != nil || r.Error != nil || r.Result == nil {
				err = fmt.Errorf("the relay refused the device")
			}
		}
	}
	if err != nil {
		c.srv.Log("  relay: open: %v", err)
		up.Close()
		return nil, relayDown()
	}
	up.SetDeadline(time.Time{})
	rs := &relaySession{c: c, up: up, pending: map[string]struct{}{}}
	c.rs = rs
	go rs.pump(br)
	return rs, nil
}

// pump hands what the relay says to the device. When the relay goes, every call it had not answered is answered
// with an error, and the next relayed call opens a new session.
func (rs *relaySession) pump(br *bufio.Reader) {
	for {
		line, err := br.ReadBytes('\n')
		if len(bytes.TrimSpace(line)) > 0 {
			rs.forward(line)
		}
		if err != nil {
			break
		}
	}
	rs.up.Close()
	rs.c.rmu.Lock()
	if rs.c.rs == rs {
		rs.c.rs = nil
	}
	rs.c.rmu.Unlock()
	rs.mu.Lock()
	rs.closed = true
	left := make([]string, 0, len(rs.pending))
	for k := range rs.pending {
		left = append(left, k)
	}
	rs.pending = nil
	rs.mu.Unlock()
	sort.Strings(left)
	for _, k := range left {
		var id any
		d := json.NewDecoder(bytes.NewReader([]byte(k)))
		d.UseNumber()
		d.Decode(&id)
		rs.c.send(map[string]any{"jsonrpc": "2.0", "id": id, "error": relayDown().wire()})
	}
}

// forward sends a line of the relay's to the device; a reply also ends its call's wait.
func (rs *relaySession) forward(line []byte) {
	var m struct {
		ID     json.RawMessage `json:"id"`
		Method string          `json:"method"`
	}
	if json.Unmarshal(line, &m) == nil && m.Method == "" && len(m.ID) > 0 {
		var id any
		d := json.NewDecoder(bytes.NewReader(m.ID))
		d.UseNumber()
		if d.Decode(&id) == nil {
			rs.mu.Lock()
			delete(rs.pending, idKey(id))
			rs.mu.Unlock()
		}
	}
	rs.c.SendRaw(bytes.TrimSpace(line))
}

func (c *Conn) closeRelay() {
	c.rmu.Lock()
	rs := c.rs
	c.rs = nil
	c.rmu.Unlock()
	if rs != nil {
		rs.up.Close()
	}
}
