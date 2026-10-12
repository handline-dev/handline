package daemon

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"regexp"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

var relayDeviceID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)

// serveRelay is the relay socket: how the gateway, the process that authenticates paired devices, calls this daemon for
// them. One connection per device connection; the first line is relay.open with the device's id, name and role, after
// that every line is the device's own call and every line sent back goes to the device. The end of the connection is the
// gateway's word that the device left or was revoked.
func (d *Daemon) serveRelay(srv *rpc.Server, ln net.Listener) {
	defer d.wg.Done()
	for {
		c, err := ln.Accept()
		if err != nil {
			if !errors.Is(err, net.ErrClosed) {
				d.cfg.Log("relay accept: %v", err)
			}
			return
		}
		if !d.track(c) {
			c.Close()
			return
		}
		go func() {
			defer d.untrack(c)
			d.relayOne(srv, c)
		}()
	}
}

func (d *Daemon) relayOne(srv *rpc.Server, c net.Conn) {
	pid := peer.PID(c)
	if err := d.gatewayPeer(pid); err != nil {
		d.cfg.Log("  relay: refused a connection from pid %d: %v", pid, err)
		return
	}
	tr := rpc.NewLineTransport(c)
	raw, err := tr.ReadMessage(rpc.RecvTimeout)
	if err != nil {
		return
	}
	var msg struct {
		JSONRPC string          `json:"jsonrpc"`
		ID      json.RawMessage `json:"id"`
		Method  string          `json:"method"`
		Params  struct {
			Device string `json:"device_id"`
			Name   string `json:"name"`
			Role   string `json:"role"`
		} `json:"params"`
	}
	if json.Unmarshal(raw, &msg) != nil || msg.JSONRPC != "2.0" || msg.Method != "relay.open" || len(msg.ID) == 0 {
		return
	}
	p := msg.Params
	name := strings.TrimSpace(p.Name)
	if !relayDeviceID.MatchString(p.Device) || p.Role != "member" || name == "" || len(p.Name) > 64 || textsafe.HasUnsafe(p.Name) {
		return
	}
	reply, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": msg.ID, "result": map[string]any{"ok": true}})
	if tr.WriteMessage(reply) != nil {
		return
	}
	srv.ServeRelayed(tr, p.Device, name)
}

// gatewayPeer is the trust check on a relay connection: the peer must be the gateway, the pid it recorded in the
// protected store (no sandbox can write it), started before that file was written (a pid the gateway left behind when
// it died and another process took is later), and not a process of any agent's session. What cannot be read is a no.
func (d *Daemon) gatewayPeer(pid int) error {
	b, err := os.ReadFile(d.cfg.PidFile)
	if err != nil {
		return fmt.Errorf("the gateway's pid file: %w", err)
	}
	st, err := os.Stat(d.cfg.PidFile)
	if err != nil {
		return fmt.Errorf("the gateway's pid file: %w", err)
	}
	want, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || want <= 1 {
		return errors.New("the gateway's pid file names no process")
	}
	if pid != want {
		return fmt.Errorf("the gateway is pid %d", want)
	}
	if started, ok := peer.Started(pid); !ok || started.After(st.ModTime()) {
		return errors.New("it did not start before the pid file was written: a reused pid")
	}
	if r, ok := d.cfg.Auth.(interface{ SessionsReadable() error }); ok {
		if err := r.SessionsReadable(); err != nil {
			return fmt.Errorf("the recorded sessions cannot be read: %w", err)
		}
	}
	if d.cfg.Auth.AgentOf(pid) != "" {
		return errors.New("it descends from an agent's session")
	}
	return nil
}
