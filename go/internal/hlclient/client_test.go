package hlclient

import (
	"bufio"
	"encoding/json"
	"errors"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/pairsock"
)

// hangUp is an answer that closes the connection instead.
const hangUp = "hang up"

// daemon serves socketpairs, so no socket file is bound: each connection's lines are kept, and answer says what to
// send back for each. dial is a client's way to it.
type daemon struct {
	ln    net.Listener
	mu    sync.Mutex
	lines []map[string]any
}

type dial = func() (net.Conn, error)

func serve(t *testing.T, answer func(msg map[string]any) string) (*daemon, dial) {
	t.Helper()
	ln := pairsock.NewListener()
	d := &daemon{ln: ln}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func() {
				defer conn.Close()
				sc := bufio.NewScanner(conn)
				for sc.Scan() {
					var m map[string]any
					_ = json.Unmarshal(sc.Bytes(), &m)
					d.mu.Lock()
					d.lines = append(d.lines, m)
					d.mu.Unlock()
					out := answer(m)
					if out == hangUp {
						return
					}
					if out != "" {
						conn.Write([]byte(out + "\n"))
					}
				}
			}()
		}
	}()
	return d, ln.Dial
}

func ok(m map[string]any, result string) string {
	b, _ := json.Marshal(m["id"])
	return `{"jsonrpc":"2.0","id":` + string(b) + `,"result":` + result + `}`
}

func client(dial dial) *Client {
	return &Client{Home: "/nowhere", Dial: dial,
		Kick: func() error { return errors.New("no service is loaded to start it (`hl service install`)") }, Wait: time.Second}
}

func TestCallSaysHelloWithTheKeyAndAnswersTheResult(t *testing.T) {
	d, to := serve(t, func(m map[string]any) string {
		if m["method"] == "hello" {
			return ok(m, `{"protocol":1}`)
		}
		return ok(m, `{"note":"half done"}`)
	})
	c := client(to)
	c.Key = "k1"
	got, err := c.Call("progress.get", nil)
	if err != nil || got["note"] != "half done" {
		t.Fatalf("got %v, %v", got, err)
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	hello := d.lines[0]["params"].(map[string]any)
	if d.lines[0]["method"] != "hello" || hello["client"] != "hl" || hello["key"] != "k1" || hello["protocol"] != 1.0 {
		t.Fatalf("hello: %v", d.lines[0])
	}
	if d.lines[1]["method"] != "progress.get" || d.lines[1]["id"] != 2.0 {
		t.Fatalf("call: %v", d.lines[1])
	}
}

func TestHelloCarriesNoKeyWhenNoneIsHeld(t *testing.T) {
	d, to := serve(t, func(m map[string]any) string { return ok(m, `{}`) })
	if _, err := client(to).Call("whoami", nil); err != nil {
		t.Fatal(err)
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, has := d.lines[0]["params"].(map[string]any)["key"]; has {
		t.Fatalf("hello: %v", d.lines[0])
	}
}

func TestARefusalIsTypedAndAWriteSaysIt(t *testing.T) {
	_, to := serve(t, func(m map[string]any) string {
		if m["method"] == "hello" {
			return ok(m, `{}`)
		}
		return `{"jsonrpc":"2.0","id":2,"error":{"code":-32000,"message":"no task in progress","data":{"code":"refused"}}}`
	})
	_, err := client(to).Call("task.progress", nil)
	var r *RPCError
	if !errors.As(err, &r) || r.Code != "refused" || r.Error() != "no task in progress" {
		t.Fatalf("got %v", err)
	}
	if done, said := client(to).Write("task.progress", map[string]any{"note": "x"}); done || said != "no task in progress" {
		t.Fatalf("write: %v %q", done, said)
	}
}

func TestAWriteCarriesAFreshRequestID(t *testing.T) {
	d, to := serve(t, func(m map[string]any) string { return ok(m, `{"said":"kept"}`) })
	c := client(to)
	for i := 0; i < 2; i++ {
		if done, said := c.Write("task.progress", map[string]any{"note": "x"}); !done || said != "kept" {
			t.Fatalf("write: %v %q", done, said)
		}
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	a, b := d.lines[1]["params"].(map[string]any), d.lines[3]["params"].(map[string]any)
	if a["note"] != "x" || len(a["request_id"].(string)) != 32 || a["request_id"] == b["request_id"] {
		t.Fatalf("params %v then %v", a, b)
	}
}

func TestAHangUpBeforeTheReplyIsNotAResult(t *testing.T) {
	_, to := serve(t, func(m map[string]any) string {
		if m["method"] == "hello" {
			return ok(m, `{}`)
		}
		return hangUp
	})
	c := client(to)
	if _, err := c.Call("x", nil); err == nil || err.Error() != "the daemon closed the connection before it replied" {
		t.Fatalf("got %v", err)
	}
	done, said := c.Write("x", nil)
	if done || said != "the daemon did not answer, so it is not known whether this was done: the daemon closed the connection before it replied" {
		t.Fatalf("write: %v %q", done, said)
	}
}

func TestNoDaemonAsksLaunchdAndWaitsForIt(t *testing.T) {
	ln := pairsock.NewListener()
	t.Cleanup(func() { ln.Close() })
	var up atomic.Bool
	c := client(func() (net.Conn, error) {
		if !up.Load() {
			return nil, errNoDaemon
		}
		return ln.Dial()
	})
	kicked := 0
	c.Kick = func() error {
		kicked++
		go func() {
			time.Sleep(100 * time.Millisecond)
			up.Store(true)
			conn, _ := ln.Accept()
			sc := bufio.NewScanner(conn)
			for i := 1; i <= 2 && sc.Scan(); i++ {
				conn.Write([]byte(`{"jsonrpc":"2.0","id":` + string(rune('0'+i)) + `,"result":{"said":"up"}}` + "\n"))
			}
			conn.Close()
		}()
		return nil
	}
	if done, said := c.Write("x", nil); !done || said != "up" || kicked != 1 {
		t.Fatalf("write %v %q, kicked %d", done, said, kicked)
	}
}

func TestADaemonThatCannotBeStartedIsDownAndNothingIsDone(t *testing.T) {
	c := client(func() (net.Conn, error) { return nil, errNoDaemon })
	c.Home = "/h"
	_, err := c.Call("x", nil)
	var down *DaemonDown
	if !errors.As(err, &down) || err.Error() !=
		"the daemon is not running: no service is loaded to start it (`hl service install`) (log: /h/logs/daemon.err.log); nothing was done" {
		t.Fatalf("got %v", err)
	}
	c.Kick = func() error { return nil }
	c.Wait = 300 * time.Millisecond
	done, said := c.Write("x", nil)
	if done || said != "daemon not running, nothing done: the daemon is not running: launchd started it but it did not answer within 0s (log: /h/logs/daemon.err.log); nothing was done" {
		t.Fatalf("write %v %q", done, said)
	}
}

// errNoDaemon is a dial that finds nothing listening.
var errNoDaemon = errors.New("connect: no such file or directory")
