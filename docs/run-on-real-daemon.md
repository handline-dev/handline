# Run Handline on the real daemon

Steps for the founder: the desktop app, a browser, an iPhone and an Android phone, all against the daemon that runs your agents.
Commands run from the repo root. `hl` is the command from [Setup](setup.md).

One plain gateway on this Mac's Tailscale address serves all of them at once: the desktop app, a browser, and the phones (a tailnet is
already encrypted, so there is no certificate to pin). Tailscale must be on for this Mac and for each phone.

Known limits of this MVP: the app cannot create a task (founder decision), and every paired device acts as you.

## 1. Start the gateway

**With the desktop app** (the usual way). It starts the Python daemon and the gateway itself, listens on this Mac's Tailscale address, advertises its `*.ts.net` name, and pairs itself.

    pnpm install && tools/build-go.sh
    pnpm app                  # or `pnpm app:main`: the app runs from its own clone of main and offers a restart when main moves

Tailscale must be up when the app starts. Without it the gateway listens on loopback only and no phone can pair.

**Without the desktop app** (a browser and phones only). Find this Mac's Tailscale address `<ip>` (a `100.x.y.z`) and its name `<name>` (`<mac>.<tailnet>.ts.net`, no trailing dot):

    tailscale ip -4
    tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'

The phones accept a plain link only for a `*.ts.net` name. A link with a bare `100.x` address is refused, so start the gateway with `-advertise <name>:7443`. Each in a terminal you keep open:

    hl daemon        # or `hl service install` once, to keep it running
    pnpm install && tools/build-go.sh && pnpm --filter @handline/web build
    go/handlined -gateway -allow-real-home -listen <ip>:7443 -advertise <name>:7443 -web apps/web/dist

You should see `network listener on <ip>:7443, plain HTTP (Tailscale)`, in the gateway's terminal (with the app, in `daemon.log` in its data folder).

**Common failure:** the gateway exits with `schema: ... does not exist: the daemon that owns it brings it up first` (or `... predates schema versions` or `... newer than the ...`).
The Python daemon is not running, or is not the version of this checkout. Start it (`hl daemon`), then the gateway. (The app starts the daemon itself.)

## 2. PC

### Desktop app

Step 1 opened it. It pairs itself, so you should see the agent list at once. `hl device list` shows this computer and `hl device revoke <id>` cancels it.

**Common failure:** the window shows "Pair this computer": the app could not pair itself. Run `hl device pair` and paste the `code:` value, or the whole `handline://` line, into the window.
If the window fails to connect, an older gateway on another address (loopback, say) is still running and the app reused it: stop it, then start the app again.
If that command says `no gateway is running (nothing listens on <home>/gateway.sock)`, the gateway is not running: step 1.

### Browser

Open `http://<ip>:7443` (the gateway you started without the app serves the page; with the app, use its window).
Run `hl device pair` and paste the code into the page. The agent list appears.

**Common failure:** "too many wrong pairing codes": this address is locked for 15 minutes after repeated wrong codes. Wait, then run `hl device pair` for a fresh code and paste it whole.

## 3. iPhone

1. Tailscale on the phone, signed in to the same tailnet.
2. Build the app onto the phone. It is a dev client, not Expo Go. Plug the iPhone in; the first build needs your Apple team set in Xcode. Build Release, so the JS is inside the app:

       cd apps/mobile && npx expo run:ios --device --configuration Release

   A Debug build (the default) loads its JS from Metro on the Mac (`npx expo start --dev-client`, port 8081): it shows nothing once that terminal is closed or if the phone cannot reach the Mac.
3. On the Mac: `hl device pair`. Send the `uri:` line to the phone (Messages, Notes) and paste the whole `handline://pair?host=<name>:7443&code=...` into the app's field, then Pair.

You should see the agent list, then an agent's page.

**Common failure:** `cannot connect to ...`: the phone cannot reach `<name>:7443` (Tailscale is off on the phone). With the gateway started without the app, the phone's browser at `http://<name>:7443` loads the page; if it does not, the network is the cause. (`<name>` is the host in the `uri:` line.)
If the app says the link has no certificate fingerprint and the address is not a Tailscale name, the gateway was started without `-advertise <name>:7443` (step 1): stop it and start it again.

`apps/mobile/README.md` documents the simulator only; `--device` builds onto a plugged-in phone. This command is not tested.

## 4. Android

1. Tailscale on the phone, signed in to the same tailnet.
2. Build the release app once (about 9 minutes) and install it on the phone with USB debugging on. `apps/mobile/android` is not in the repo, so generate it first.

       cd apps/mobile && npx expo prebuild --platform android
       cd android && ./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
       adb install -r app/build/outputs/apk/release/app-release.apk

   Skip `adb reverse`: the phone reaches `<name>` itself. Open the app from the launcher.
3. On the Mac: `hl device pair`, send the `uri:` line to the phone, paste it into the app, Pair.

You should see the agent list.

**Common failure:** the same as the iPhone's: the phone cannot reach `<name>:7443`, or the link carries a bare `100.x` address (the gateway was started without `-advertise <name>:7443`).

## Not on Tailscale: a LAN with a certificate

Without Tailscale the gateway must serve TLS and the phone pins its certificate. This gateway cannot serve the desktop app (it speaks plain `ws://` only); use a browser on the Mac.
Make a certificate once (`<lan-ip>` is this Mac's Wi-Fi address, which the phone reaches on the same network), then start the gateway with it:

    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 -subj "/CN=handline" \
      -addext "subjectAltName=IP:<lan-ip>" -keyout ~/handline.key -out ~/handline.crt
    go/handlined -gateway -allow-real-home -listen <lan-ip>:7443 -cert ~/handline.crt -key ~/handline.key -web apps/web/dist

`hl device pair` now prints a link with a fingerprint, which the phone checks. A browser at `https://<lan-ip>:7443` warns about the certificate once.
"Certificate does not match" in the app means the link came from a gateway with a different certificate: run `hl device pair` again.
