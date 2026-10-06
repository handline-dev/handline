# @handline/web

Browser shell on `@handline/app-react`. From this directory:

    HANDLINE_FAKE=1 ../../node_modules/.bin/vite

Open `http://127.0.0.1:5173/?daemon=ws://127.0.0.1:7790` (`?daemon=` picks the daemon; `HANDLINE_FAKE=1` serves the fake daemon on that port). Set `TURBO_TELEMETRY_DISABLED=1` when running `turbo test typecheck`.
