// Package hlclient is the hl command's way to the daemon (handline/infra/daemonclient.py and handline/cli/client.py): one
// JSON-RPC call on a fresh connection, hello first, as the founder when this terminal holds the founder key and as the
// agent when it runs in an agent's session (the daemon tells which from the process, not from anything said here).
package hlclient

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/pairsock"
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
	// As is the name hello gives, Name when empty; Deadline a call's, Timeout when zero.
	As       string
	Deadline time.Duration
}

// New is the client for this process's home, holding the founder key when this terminal may read it.
func New() *Client {
	home := paths.Home()
	key, _ := protected.New(home).Get(founderNS, "key")
	return &Client{
		Home: home,
		Key:  strings.TrimSpace(key),
		Dial: Dialer(home, 5*time.Second),
		Kick: kickstart,
		Wait: startWait,
	}
}

// Dialer connects to home's daemon socket within timeout. Under a test root, HANDLINE_DAEMON_FD names an inherited
// unix socket (pairsock.Listener.Control) that each dial sends its connection over instead, so the rig binds no file.
func Dialer(home string, timeout time.Duration) func() (net.Conn, error) {
	if over := rigConn(); over != nil {
		return pairsock.DialOver(over)
	}
	sock := paths.SocketFile(home)
	return func() (net.Conn, error) { return net.DialTimeout("unix", sock, timeout) }
}

// rigConn is the inherited HANDLINE_DAEMON_FD socket, taken once a process: a second os.File on the fd would close it
// when collected.
var rigConn = sync.OnceValue(takeRigConn)

func takeRigConn() *net.UnixConn {
	root, _ := paths.Getenv("TEST_ROOT")
	v, _ := paths.Getenv("DAEMON_FD")
	fd, err := strconv.Atoi(v)
	if root == "" || err != nil {
		return nil
	}
	f := os.NewFile(uintptr(fd), "daemon-fd")
	defer f.Close()
	c, err := net.FileConn(f)
	uc, _ := c.(*net.UnixConn)
	if err != nil {
		return nil
	}
	return uc
}

// launchctl runs launchctl with args: its words, and whether it failed. Tests give their own.
var launchctl = func(args ...string) (string, error) {
	out, err := exec.Command("launchctl", args...).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// kickstart asks launchd to start the installed service; this never starts a daemon itself, so there is never a second one.
// A plist this process cannot see, a sandboxed agent's case, is not taken as installed. One installed and not loaded (after a
// bootout, or a login that did not load it) is loaded as it is, never rewritten, and started (handline/infra/daemonclient.ensure_daemon).
func kickstart() error {
	plist := filepath.Join(paths.UserHome(), "Library", "LaunchAgents", label+".plist")
	if _, err := os.Stat(plist); err != nil {
		return errors.New("no service is installed to start it (`hl service install`)")
	}
	domain := fmt.Sprintf("gui/%d", os.Getuid())
	target := domain + "/" + label
	said, err := launchctl("kickstart", target)
	if err != nil && strings.Contains(strings.ToLower(said), "could not find service") {
		if why, err := launchctl("bootstrap", domain, plist); err != nil {
			if why == "" {
				why = "launchctl bootstrap failed"
			}
			return fmt.Errorf("the service is installed but not loaded, and launchd would not load it: %s (`hl service install`)", cut(why, 200))
		}
		_, _ = launchctl("enable", target)
		said, err = launchctl("kickstart", target)
	}
	if err == nil {
		return nil
	}
	if said == "" {
		said = "no reason given"
	}
	return fmt.Errorf("launchd would not start it: %s", cut(said, 200))
}

// cut is s[:n] in characters, as Python slices text.
func cut(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
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
	deadline, as := c.Deadline, c.As
	if deadline == 0 {
		deadline = Timeout
	}
	if as == "" {
		as = Name
	}
	_ = conn.SetDeadline(time.Now().Add(deadline))
	hello := map[string]any{"client": as, "protocol": 1}
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

// Legacy sends one legacy socket verb and answers the daemon's `{ok, said}` reply, nil when it could not be sent or read
// (handline/infra/daemonclient.send). The daemon answers and hangs up, so a refused shutdown of the write side is no failure.
func (c *Client) Legacy(payload map[string]any, timeout time.Duration) map[string]any {
	conn, err := c.Connect()
	if err != nil {
		return nil
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(timeout))
	b, err := json.Marshal(payload)
	if err != nil {
		return nil
	}
	if _, err := conn.Write(append(b, '\n')); err != nil {
		return nil
	}
	if uc, ok := conn.(*net.UnixConn); ok {
		_ = uc.CloseWrite()
	}
	data, err := io.ReadAll(conn)
	if err != nil {
		return nil
	}
	var reply map[string]any
	if json.Unmarshal(bytes.TrimSpace(data), &reply) != nil {
		return nil
	}
	return reply
}

func newID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
