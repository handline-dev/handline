# The Go daemon (`handlined`)

One binary, built from `go/`. Run it with `-home DIR`; on the default home it refuses to start unless `-allow-real-home` says so.
`tools/test-go.sh` runs `go test -race`, a cgo-free build and the contract fixtures against the built binary.

## Network listener

`-listen ADDR:PORT` opens an encrypted WebSocket (`/rpc`) for paired devices; nothing listens without it. `ADDR` must be an IP of this
machine's loopback, LAN or Tailscale network, never a wildcard, a public address or a host name. A connection no device token opened gets
one refusal for every call (`gate: unpaired`) and nothing else. `device.pair` (founder) makes a one-time code; a device trades it
with `device.redeem` for a token, which is kept as a hash in the `devices` table and sent in `hello`.

Without `-cert` and `-key` the listener keeps a self-signed certificate in the home (`network.crt`, `network.key`).

## Gateway mode

`-gateway` makes the daemon the network front door of the Python daemon that owns the home: Python stays the only writer of agent state.

    handlined -gateway -allow-real-home -listen 100.64.1.2:7443 -cert HOST.crt -key HOST.key \
              -advertise HOST.TAILNET.ts.net:7443 -web /path/to/web/dist

- It opens `db.sqlite` without creating or migrating it and refuses one the Python daemon has not brought to this schema.
- It writes the `devices` table and its pid file, nothing else. The pid file is in the founder's protected store, which no agent sandbox
  can write (`~/.config/orcapipe/protected/<home slug>/gateway/pid`, directory 0700, file 0600, written atomically, removed at exit; one live
  gateway at a time; `-pid-file` moves it, and only a test should):
  no hook spool, no `hook.record`, no generic write events. Its local socket is `gateway.sock`, for the founder's device commands, never `daemon.sock`.
- It serves natively `hello`, `whoami`, the device methods and the methods named by `-native` (default: the area-1 reads
  `daemon.status,agents.list,agent.get,agent.timeline,tasks.list,task.get,inbox.list`). Every other method is **relayed**: a method that
  moves from relay to Go is added to `-native` once its fixtures pass against this daemon.
- **Relay.** A paired device's call for a method that is not native is checked here against `contract/protocol.json` (a generated
  copy, `go/internal/rpc/contract.json`, kept current by `tools/go_contract.py`): a name the contract lacks is unknown, a method
  above member or whose callers leave members out is refused (`gate: role`), and neither is sent on. The rest goes to `relay.sock`
  (`-relay-socket`) on a connection of its own for each device connection: the first line is
  `{"jsonrpc":"2.0","id":0,"method":"relay.open","params":{"device_id","name","role":"member"}}`, answered `{"result":{"ok":true}}`; after it every
  line from the device goes to the relay as it is and every line the relay sends (replies and notifications) goes to the device as it is. Before it sends `relay.open` it checks that the process answering on `relay.sock` is the one named in `<home>/locks/daemon.lock`
  (the peer pid of the connection), since anyone can rebind that path. A closed
  relay connection is how Python learns a device hung up or was revoked. If the relay is down, or drops with a call unanswered, the
  call fails with `daemon_busy` (retry after 2 s) and the next call opens a new connection.

## Web UI and TLS

`-web DIR` serves the built web UI on the listener's own origin (`https://HOST:PORT/`), so a browser needs no pinning. Files only: no
listing, nothing outside `DIR` (including through a symlink), no dotfiles, `index.html` never cached, `GET` and `HEAD` only, a `token` in the
query refused. The page is the app and its pairing screen, not data; every call it makes goes through the device gate, with the token sent
in the first `hello` message. A browser's WebSocket is accepted only from the page this listener served.

The certificate is read from `-cert` and `-key` and read again whenever either file changes, so a renewal needs no restart. Nothing else is read or
created, and neither is logged. Two setups:

1. **CA-trusted (browsers).** `tailscale cert HOST.TAILNET.ts.net` writes `HOST.TAILNET.ts.net.crt` and `.key`; give those to `-cert`/`-key` and
   `-advertise HOST.TAILNET.ts.net:PORT`. Browsers accept it without a warning.
2. **Self-signed (native clients that pin).** Any self-signed certificate and key (for example from `openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 ...`).
   The pairing code carries its sha256 fingerprint and clients refuse any other certificate. A browser warns once.

`device.pair` returns `{code, expires_at, fingerprint, uri}`; `uri` is `handline://pair?host=ADDRESS&fp=FINGERPRINT&code=CODE`, the address
being `-advertise` or the listening address.
