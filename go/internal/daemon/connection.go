package daemon

import (
	"net"
	"strings"

	"github.com/handline/handline/go/internal/heavyslots"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/usage"
)

// connectionGet is connection.get: what this process knows of how it is reached, read only. A daemon behind a gateway has no listener of
// its own, so its answer has none; the gateway's local socket answers with the gateway's.
func (d *Daemon) connectionGet(_ *rpc.Conn, _ map[string]any) (any, error) {
	var listener any
	if d.dev != nil {
		if addr, fp := d.dev.Listener(); addr != "" {
			l := map[string]any{"address": addr, "encryption": "tls", "fingerprint": fp, "tailscale_host": nil}
			if d.cfg.plain() {
				l["encryption"], l["fingerprint"] = "none", nil
			}
			if host, _, err := net.SplitHostPort(addr); err == nil && strings.HasSuffix(host, ".ts.net") {
				l["tailscale_host"] = host
			}
			listener = l
		}
	}
	served := "daemon"
	if d.cfg.Gateway {
		served = "gateway"
	}
	slots := map[string]any{"slots": heavyslots.Slots(), "dir": nil}
	if dir, err := heavyslots.Dir(); err == nil {
		slots["dir"] = dir
	}
	return map[string]any{"served_by": served, "listener": listener, "heavy_slots": slots,
		"dev_mode": reads.DevMode(usage.LoadConfig(d.cfg.Home))}, nil
}
