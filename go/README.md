# The Go daemon (`handlined`)

One binary, built from `go/`. Run it with `-home DIR`; on the default home it refuses to start unless `-allow-real-home` says so.
`tools/test-go.sh` runs `go test -race`, a cgo-free build and the contract fixtures against the built binary.

## Network listener

`-listen ADDR:PORT` opens an encrypted WebSocket (a gateway without `-cert` is plain, see Tailscale mode) (`/rpc`) for paired devices; nothing listens without it. `ADDR` must be an IP of this
machine's loopback, LAN or Tailscale network, never a wildcard, a public address or a host name. A connection no device token opened gets
one refusal for every call (`gate: unpaired`) and nothing else. `device.pair` (founder) makes a one-time code; a device trades it
with `device.redeem` for a token, which is kept as a hash in the `devices` table and sent in `hello`.

Without `-cert` and `-key` the listener keeps a self-signed certificate in the home (`network.crt`, `network.key`).

## Gateway mode

`-gateway` makes the daemon the network front door of the Python daemon that owns the home: Python stays the only writer of agent state.

    handlined -gateway -allow-real-home -listen :7443 -web /path/to/web/dist

With no `-cert` and `-key` this is Tailscale mode (below): nothing to set up.

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
- **The devices topic** is the gateway's, in memory: `device.redeem` and `device.revoke` send `device.paired` and `device.revoked` to the
  founder connections subscribed to `devices`, with cursor 0; a client reads `devices.list` again after a reconnect. A list naming
  `devices` must name nothing else (`bad_request`, field `topics`: devices and transcript are live from memory, the rest come from the log),
  and a device naming `devices` alone is refused (`gate: role`).
- **The other topics** are rows of `stream_log`, whose id is the cursor, and `subscribe` and `unsubscribe` serve them here, on the gateway too
  (nothing is relayed). Rows come from any writer: this daemon's `Hub.Append`, and the Python daemon's watcher until it stops. One poll of
  `MAX(id)` every 0.25 s (or at once after an Append) hands the new rows to the subscribers in cursor order; there are no change detectors
  here, so a topic's rows still come from Python until its detector moves. A resume replays up to 5000 events and then sends `gap`
  (`retention`, which is also a cursor older than `stream:pruned`); a subscriber more than 1000 live events behind gets `gap` (`slow_client`)
  and is hung up. An agent session is sent what its role may read (`stream.AgentMay`). Retention is 7 days or 50000 rows, pruned hourly.
  The detectors that turn database changes into rows are `internal/watcher`: one per topic, each moved here from the Python watcher, and started
  only with `-structured-tick -cutover` (until then Python writes the rows and a second writer would double them). Moved so far: tasks.

## Web UI and TLS

`-web DIR` serves the built web UI on the listener's own origin (`http://` in Tailscale mode, `https://` with a certificate), so a browser needs no pinning. Files only: no
listing, nothing outside `DIR` (including through a symlink), no dotfiles, `index.html` never cached, `GET` and `HEAD` only, a `token` in the
query refused. The page is the app and its pairing screen, not data; every call it makes goes through the device gate, with the token sent
in the first `hello` message. A browser's WebSocket is accepted only from the page this listener served.

### Tailscale mode (the default): no certificate

A gateway given no `-cert` and `-key` serves plain HTTP and WebSocket, because a tailnet is already encrypted end to end by WireGuard.
There is nothing to install and a browser shows no warning.

- `-listen :PORT` binds the address of the machine's Tailscale interface (`utun*` on macOS, `tailscale*` elsewhere; an address in 100.64.0.0/10 on any other interface does not count, since some networks hand that range out on the LAN) only. If there is none, the gateway refuses to start; it
  never falls back to `0.0.0.0` or to another interface. `-listen 100.x.y.z:PORT` names the address yourself, and it must be that interface's.
- Plain HTTP is served only on a Tailscale interface address or on loopback (for the proxy below). Any other address, a LAN address for example, is refused
  at start (`-cert` and `-key` are needed there). There is no way to serve plain HTTP on the LAN.
- The pairing code carries the address and a one-time secret, and no fingerprint (there is no certificate). Pairing secrets and tokens are
  never logged. A caller no device token has opened still gets no data.
- A browser's WebSocket is accepted only from the page the listener served (`http://HOST:PORT`).

**Secure context (installable web app, service worker).** A browser treats plain `http://` on a tailnet address as not secure, so a web
app cannot be installed from it. For that, run once, with the gateway on loopback (`-listen 127.0.0.1:7443`):

    tailscale serve --bg --https=443 http://127.0.0.1:7443

Tailscale then serves `https://HOST.TAILNET.ts.net/` with its own certificate and proxies to the gateway; use `-advertise HOST.TAILNET.ts.net:443`.
I could not verify this command's syntax offline; check it against the current Tailscale documentation (`tailscale serve --help`) before relying on it.
Skip it if you do not want the app installed.

### TLS (any other interface, or native clients that pin)

With `-cert` and `-key` the listener serves TLS 1.3 on any LAN, Tailscale or loopback address, never plain. The certificate is read from those
files and read again whenever either file changes, so a renewal needs no restart. Nothing else is read or created, and neither file is logged.
Both files are given together or not at all.

- **Self-signed, pinned.** Any self-signed certificate and key (for example from
  `openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 ...`). The pairing code carries its sha256 fingerprint and
  a native client refuses any other certificate. A browser warns once.
- **CA-trusted.** `tailscale cert HOST.TAILNET.ts.net` writes `HOST.TAILNET.ts.net.crt` and `.key`; give those to `-cert`/`-key` and
  `-advertise HOST.TAILNET.ts.net:PORT`. Browsers accept it without a warning.

`device.pair` returns `{code, expires_at, fingerprint, uri}`; `uri` is `handline://pair?host=ADDRESS&fp=FINGERPRINT&code=CODE`, the address
being `-advertise` or the listening address; `fp` is absent in Tailscale mode.
