# Run Handline on the real daemon

Steps for the founder: the desktop app, a browser, an iPhone and an Android phone, all against the daemon that runs your agents.
Commands run from the repo root. `hl` is the command from [Setup](setup.md).

**One gateway runs at a time, and it has one mode.** The desktop app only speaks plain `ws://`; the phone apps only speak `wss://` to a
certificate they pin. So:

- **Mode A, plain on this Mac**: the desktop app and a browser on this Mac.
- **Mode B, TLS**: the phones, and a browser (on the Mac or on a phone). The desktop app cannot connect in this mode.

Stop the gateway (`Ctrl-C`) to switch. The phone app cannot pair with a Tailscale-mode (plain) gateway: its pairing link has no
fingerprint and the app refuses it (`not a handline pairing uri`).

Known limits of this MVP: the app cannot create a task (founder decision), and every paired device acts as you.

## 1. Start the gateway

1. Start the daemon, which owns the home and its database. Leave it running.

       hl service install        # or `hl daemon` in a terminal you keep open

2. Build the gateway and the web page it serves.

       tools/build-go.sh
       pnpm install && pnpm --filter @handline/web build

3. Start it in the mode you need, in a terminal you keep open.

   Mode A:

       go/handlined -gateway -allow-real-home -listen 127.0.0.1:7443 -web apps/web/dist

   Mode B: make a certificate once (`<ip>` is the address the phone reaches this Mac on: its Tailscale `100.x.y.z`, or its Wi-Fi address on the same network).

       openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 -subj "/CN=handline" \
         -addext "subjectAltName=IP:<ip>" -keyout ~/handline.key -out ~/handline.crt
       go/handlined -gateway -allow-real-home -listen <ip>:7443 -cert ~/handline.crt -key ~/handline.key -web apps/web/dist

You should see `network listener on 127.0.0.1:7443, plain HTTP (Tailscale)` (mode A) or `network listener on <ip>:7443, certificate <hex>` (mode B).

**Common failure:** the gateway exits with a message about the database. The Python daemon is not running, or is older than this
checkout. Run step 1 again, then restart the gateway.

## 2. PC

### Desktop app (mode A)

1. `pnpm app`
2. The window opens on "Pair this computer". In another terminal: `hl device pair`
3. Paste the `code:` value, or the whole `handline://` line, into the window.

You should see the agent list. The next launch goes straight in; `hl device list` shows this computer, `hl device revoke <id>` cancels it.

**Common failure:** `hl device pair` says the gateway did not answer. The gateway is not running, or runs on another home. Start it (step 1).
If the window asks for a code again, the old one was used or expired (they are one time): run `hl device pair` again.

### Browser

Mode A: open `http://127.0.0.1:7443`. Mode B: open `https://<ip>:7443` and accept the certificate warning once.
Then `hl device pair` and paste the code into the page, as above. The agent list appears.

**Common failure:** "too many wrong pairing codes": this address is locked for 15 minutes after repeated wrong codes. Wait, then run `hl device pair` for a fresh code and paste it whole.

## 3. iPhone (mode B)

1. Start the gateway in mode B. Have Tailscale on the phone if `<ip>` is the Tailscale address, or the same Wi-Fi if it is the LAN one.
2. Build the app onto the phone. It is a dev client, not Expo Go. Plug the iPhone in; the first build needs your Apple team set in Xcode.

       cd apps/mobile && npx expo run:ios --device

3. On the Mac: `hl device pair`. Send the `uri:` line to the phone (Messages, Notes) and paste the whole `handline://pair?...` into the app's field, then Pair.
   The app's hint says "Settings, Pair device"; there is no such screen, `hl device pair` is the step.

You should see the agent list, then an agent's page.

**Common failure:** `cannot connect to wss://<ip>:7443/rpc`: the phone cannot reach the address (Tailscale is off on the phone, or another Wi-Fi).
"The certificate does not match" means the link came from a gateway with a different certificate: run `hl device pair` again.

`apps/mobile/README.md` documents the simulator only; `--device` is the same build onto a plugged-in phone.

## 4. Android (mode B)

1. Start the gateway in mode B, as for the iPhone.
2. Build the release app once (about 9 minutes) and install it on the phone with USB debugging on.

       cd apps/mobile/android && ./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
       adb install -r app/build/outputs/apk/release/app-release.apk

   Skip `adb reverse`: the phone reaches `<ip>` itself. Open the app from the launcher.
3. On the Mac: `hl device pair`, send the `uri:` line to the phone, paste it into the app, Pair.

You should see the agent list. A link whose fingerprint differs from the gateway's is refused with "certificate does not match".

**Common failure:** the same as the iPhone's: the phone cannot reach `<ip>:7443`. Test it in the phone's browser at `https://<ip>:7443`; if the page does not load, the network is the cause.
