// Package hlclient is the hl command's way to the daemon (handline/daemon/client.py and handline/cli/client.py): one
// JSON-RPC call on a fresh connection, hello first, as the founder when this terminal holds the founder key and as the
// agent when it runs in an agent's session (the daemon tells which from the process, not from anything said here).
package hlclient

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
)

const (
	Name = "hl"
	// Timeout is a call's deadline: a wake can take up to ~450s, and shorter would read one still running as a daemon that is down.
	Timeout   = 480 * time.Second
	startWait = 15 * time.Second
	startPoll = 200 * time.Millisecond
	label     = "dev.orcapipe.daemon"
	founderNS = "founder-keys"
)

// DaemonDown is a daemon that is not there and could not be started. Nothing was done.
type DaemonDown struct{ Why, Log string }

func (e *DaemonDown) Error() string {
	return fmt.Sprintf("the daemon is not running: %s (log: %s); nothing was done", e.Why, e.Log)
}

// RPCError is the daemon's typed refusal.
type RPCError struct {
	Code    string
	Message string
	Data    map[string]any
}

func (e *RPCError) Error() string {
	if e.Message != "" {
		return e.Message
	}
	return e.Code
}

// Client reaches one home's daemon. Dial and Kick are the socket and launchd; tests give their own.
type Client struct {
	Home string
	Key  string
	Dial func() (net.Conn, error)
	Kick func() error
	Wait time.Duration
}

// New is the client for this process's home, holding the founder key when this terminal may read it.
func New() *Client {
	home := paths.Home()
	key, _ := protected.New(home).Get(founderNS, "key")
	sock := paths.SocketFile(home)
	return &Client{
		Home: home,
		Key:  strings.TrimSpace(key),
		Dial: func() (net.Conn, error) { return net.DialTimeout("unix", sock, 5*time.Second) },
		Kick: kickstart,
		Wait: startWait,
	}
}

// kickstart asks launchd to start the installed service; this never starts a daemon itself, so there is never a second one.
// A plist this process cannot see, a sandboxed agent's case, is not taken as installed.
func kickstart() error {
	if _, err := os.Stat(filepath.Join(paths.UserHome(), "Library", "LaunchAgents", label+".plist")); err != nil {
		return errors.New("no service is installed to start it (`hl service install`)")
	}
	out, err := exec.Command("launchctl", "kickstart", fmt.Sprintf("gui/%d/%s", os.Getuid(), label)).CombinedOutput()
	if err == nil {
		return nil
	}
	said := strings.TrimSpace(string(out))
	if strings.Contains(strings.ToLower(said), "could not find service") {
		return errors.New("no service is loaded to start it (`hl service install`)")
	}
	if said == "" {
		said = "no reason given"
	}
	if len(said) > 200 {
		said = said[:200]
	}
	return fmt.Errorf("launchd would not start it: %s", said)
}

// Connect is a connected socket, asking launchd to start the daemon when nothing listens.
func (c *Client) Connect() (net.Conn, error) {
	if conn, err := c.Dial(); err == nil {
		return conn, nil
	}
	down := func(why string) error { return &DaemonDown{why, filepath.Join(c.Home, "logs", "daemon.err.log")} }
	if err := c.Kick(); err != nil {
		return nil, down(err.Error())
	}
	deadline := time.Now().Add(c.Wait)
	for time.Now().Before(deadline) {
		if conn, err := c.Dial(); err == nil {
			return conn, nil
		}
		time.Sleep(startPoll)
	}
	return nil, down(fmt.Sprintf("launchd started it but it did not answer within %.0fs", c.Wait.Seconds()))
}

type reply struct {
	ID     int             `json:"id"`
	Result json.RawMessage `json:"result"`
	Error  *struct {
		Message string         `json:"message"`
		Data    map[string]any `json:"data"`
	} `json:"error"`
}

func (r *reply) err() error {
	if r.Error == nil {
		return nil
	}
	code, _ := r.Error.Data["code"].(string)
	if code == "" {
		code = "internal"
	}
	return &RPCError{Code: code, Message: r.Error.Message, Data: r.Error.Data}
}

// Call is the result of method. A *DaemonDown when the daemon is not there, an *RPCError when it refuses.
func (c *Client) Call(method string, params map[string]any) (map[string]any, error) {
	conn, err := c.Connect()
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(Timeout))
	hello := map[string]any{"client": Name, "protocol": 1}
	if c.Key != "" {
		hello["key"] = c.Key
	}
	if params == nil {
		params = map[string]any{}
	}
	var out []byte
	for i, m := range []map[string]any{
		{"jsonrpc": "2.0", "id": 1, "method": "hello", "params": hello},
		{"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
	} {
		b, err := json.Marshal(m)
		if err != nil {
			return nil, fmt.Errorf("message %d: %w", i+1, err)
		}
		out = append(append(out, b...), '\n')
	}
	if _, err := conn.Write(out); err != nil {
		return nil, err
	}
	sc := bufio.NewScanner(conn)
	sc.Buffer(make([]byte, 0, 64<<10), 64<<20)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var r reply
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			return nil, fmt.Errorf("the daemon sent what is not JSON: %w", err)
		}
		if err := r.err(); err != nil && (r.ID == 1 || r.ID == 2) {
			return nil, err
		}
		if r.ID == 2 {
			var res map[string]any
			if err := json.Unmarshal(r.Result, &res); err != nil {
				return nil, fmt.Errorf("the daemon's result is not an object: %w", err)
			}
			return res, nil
		}
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	return nil, errors.New("the daemon closed the connection before it replied")
}

// Write is a write under a fresh request_id: (true, what it said) or (false, why it was refused or not made).
func (c *Client) Write(method string, params map[string]any) (bool, string) {
	ok, said, _ := c.Written(method, params)
	return ok, said
}

// Written is Write, and the whole result beside the words (nil when refused or not made).
func (c *Client) Written(method string, params map[string]any) (bool, string, map[string]any) {
	p := map[string]any{"request_id": newID()}
	for k, v := range params {
		p[k] = v
	}
	got, err := c.Call(method, p)
	var down *DaemonDown
	var refused *RPCError
	switch {
	case errors.As(err, &down):
		return false, "daemon not running, nothing done: " + down.Error(), nil
	case errors.As(err, &refused):
		return false, refused.Error(), nil
	case err != nil:
		return false, "the daemon did not answer, so it is not known whether this was done: " + err.Error(), nil
	}
	said, _ := got["said"].(string)
	return true, said, got
}

func newID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
