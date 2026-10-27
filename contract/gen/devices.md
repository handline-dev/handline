<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# devices methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `devices.list` | founder |  | none | `{devices}` |  |  |
| `device.pair` | founder | yes | `name?`, `ttl_s?`, `request_id` | `Accepted & {code, expires_at, fingerprint, uri}` | `refused` |  |
| `device.redeem` | anonymous | yes | `code`, `name`, `request_id` | `Accepted & {token, device}` | `refused` | pairing_code, attempt_limit |
| `device.revoke` | founder | yes | `device`, `request_id` | `Accepted` | `refused`, `not_found` |  |

### `devices.list`

Paired devices with last seen and whether each is connected.

### `device.pair`

Make a one-time pairing code. Founder only. The code is single use and expires: `ttl_s` seconds (default 300, at most 600). The reply carries the listener certificate fingerprint and the address in `uri`, which is what the QR holds; a client pins that fingerprint and refuses any other certificate. It is not replayed by request_id: a retry with the same request_id runs again and makes another code.

### `device.redeem`

Trade a pairing code for a device token. The only method an unpaired network caller may call. A code that is wrong, expired or already used is refused, gate `pairing_code`, without saying which; too many wrong tries are refused, gate `attempt_limit`. A code redeems once: the token is in this one reply and never again, and the daemon keeps only its hash. `name` is how the device is listed. It is not replayed by request_id either: a retry runs again, and a code that was redeemed stays used, so the retry is refused. The wrong-code count for a redeem over the local socket is kept under the host '' (the empty string), since it has no address.

### `device.revoke`

Cancel a device and close its connections at once. Cancels the device's token. Its open connections and terminal attaches are closed at once, its next call is refused (gate `unpaired`), and a device.revoked event is sent on the devices topic. Revoking a device that is not paired is not_found.

