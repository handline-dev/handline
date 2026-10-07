package devices

import (
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// Emit puts an event on the devices topic; actor is nil for none. A nil Emit sends none.
type Emit func(typ string, data map[string]any, actor map[string]any, title string)

// Register adds the device methods to a server. m is nil when the daemon has no network listener: there is
// nothing to pair with, and the methods say so.
func Register(s *rpc.Server, m *Manager, emit Emit) {
	if emit == nil {
		emit = func(string, map[string]any, map[string]any, string) {}
	}
	off := func() error { return rpc.Refused("the network listener is off", "network", "") }
	s.Register("device.pair", rpc.Method{Role: "founder", Write: true, Unkept: true, Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		if m == nil {
			return nil, off()
		}
		ttl := DefaultTTL
		if v, has := p["ttl_s"]; has {
			n, ok := rpc.AsInt(v)
			if !ok || n < 1 || n > int64(MaxTTL/time.Second) {
				return nil, rpc.BadRequestError("ttl_s is a whole number of seconds from 1 to 600", "ttl_s", rpc.InvalidParams)
			}
			ttl = time.Duration(n) * time.Second
		}
		// name is only a hint for the one who scans the code: the device is listed under the name it redeems with.
		code, expires, print, uri := m.Pair(ttl)
		return map[string]any{"accepted": true, "code": code, "expires_at": expires.UTC().Format("2006-01-02T15:04:05Z"),
			"fingerprint": print, "uri": uri}, nil
	}})
	s.Register("device.redeem", rpc.Method{Role: "anonymous", Write: true, Unkept: true, Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		code, ok := p["code"].(string)
		if !ok {
			return nil, rpc.BadRequestError("code is a string", "code", rpc.InvalidParams)
		}
		name, ok := p["name"].(string)
		if !ok || name == "" {
			return nil, rpc.BadRequestError("name is the device's name, a string", "name", rpc.InvalidParams)
		}
		if m == nil {
			return nil, rpc.Refused("that pairing code is not valid", "pairing_code", "")
		}
		token, d, err := m.Redeem(code, name, c.Remote)
		if err != nil {
			return nil, err
		}
		emit("device.paired", m.Row(d), nil, d.Name+" paired")
		return map[string]any{"accepted": true, "token": token, "device": d.ID}, nil
	}})
	s.Register("device.revoke", rpc.Method{Role: "founder", Write: true, Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		id, ok := p["device"].(string)
		if !ok {
			return nil, rpc.BadRequestError("device is a string", "device", rpc.InvalidParams)
		}
		if m == nil {
			return nil, rpc.NotFoundError("no such device: " + id)
		}
		found, err := m.Revoke(id)
		if err != nil {
			return nil, err
		}
		if !found {
			return nil, rpc.NotFoundError("no such device: " + id)
		}
		emit("device.revoked", map[string]any{"device": id}, c.Caller.ActorInfo(), "device "+id+" revoked")
		return map[string]any{"accepted": true}, nil
	}})
	s.Register("devices.list", rpc.Method{Role: "founder", Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		list := []map[string]any{}
		if m != nil {
			var err error
			if list, err = m.List(); err != nil {
				return nil, err
			}
		}
		return map[string]any{"devices": list}, nil
	}})
}
