// The Handline.app in /Applications: a thin launcher that does what `pnpm app` does, with no terminal (apps/electron/app-install.ts writes it).
// The pieces are plain data and text, so a test can read them.
import path from 'node:path';

export const LAUNCHER_ID = 'dev.handline.launcher';

/** Where the app's own checkout of main, the launcher's log and the Handline bundles live. */
export const dataDir = (home: string): string => path.join(home, '.local', 'share', 'handline');

/** The launcher bundle's Info.plist: no Dock icon of its own (the app it starts has one), the Handline icon for Finder and the Dock's pin. */
export function infoPlist(): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Handline</string>
  <key>CFBundleDisplayName</key><string>Handline</string>
  <key>CFBundleExecutable</key><string>Handline</string>
  <key>CFBundleIdentifier</key><string>${LAUNCHER_ID}</string>
  <key>CFBundleIconFile</key><string>Handline</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
`;
}

/**
 * The launcher's executable. A second open (the script runs again, because it ended) only brings the running app to the front. Otherwise it takes the
 * login shell's PATH (a Dock launch has none), tells the person it is starting, and starts follow-start.ts from the app's checkout in the background, so
 * the script ends and the next open runs it again. Everything it says goes to the log.
 */
export function launcherScript(): string {
  return `#!/bin/bash
# Handline.app: starts Handline from its own checkout of main (the same as pnpm app) and keeps it following main.
DATA="$HOME/.local/share/handline"
APP_MAIN="\${HANDLINE_FOLLOW_DIR:-$DATA/app-main}"
mkdir -p "$DATA"
exec >>"$DATA/launcher.log" 2>&1
echo "== $(date) open"
notify() { osascript -e "display notification \\"$1\\" with title \\"Handline\\"" >/dev/null 2>&1; }

# Already running: bring its window to the front.
running=$(pgrep -f 'Handline-[0-9.]+\\.app/Contents/MacOS/Electron( |$)' | head -1)
if [ -n "$running" ]; then
  exe=$(ps -o command= -p "$running")
  open "\${exe%/Contents/MacOS/*}"
  exit 0
fi

# Still starting (fetching, installing, building: no window yet): a second open does not start a second run on the same checkout.
if pgrep -f 'apps/electron/follow-start\\.ts' >/dev/null; then
  echo "already starting"
  notify "Handline is already starting. Its window opens when the update is built."
  exit 0
fi

# A Dock launch has no shell PATH: ask the login shell for one.
login=$("\${SHELL:-/bin/zsh}" -ilc 'echo __PATH__$PATH' 2>/dev/null | sed -n 's/.*__PATH__//p' | tail -1)
export PATH="$login:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

if [ ! -d "$APP_MAIN/.git" ]; then
  echo "no checkout at $APP_MAIN: run pnpm app once from the repo"
  notify "Handline cannot start: run pnpm app once from the repo first."
  exit 1
fi
export HANDLINE_SOURCE_GIT=$(git -C "$APP_MAIN" remote get-url origin)
notify "Handline is starting. It brings itself up to the latest main first, which can take a few minutes."
cd "$APP_MAIN" || exit 1
nohup bash -c 'node apps/electron/follow-start.ts || osascript -e "display notification \\"Handline could not start. See ~/.local/share/handline/launcher.log\\" with title \\"Handline\\""' >>"$DATA/launcher.log" 2>&1 &
`;
}

/**
 * The command that starts the app again once the running one has quit. It polls for the old process, not once: it is a detached child, so it has nothing to wait on
 * (and the new app would meet the old one's single-instance lock). A window already open on an old-style bundle keeps showing what it has until it is restarted.
 * It runs launch.ts, so the Handline bundle of a new Electron version is made and used.
 */
export function relaunchCommand(pid: number, electronDir: string): { file: string; args: string[] } {
  return {
    file: 'sh',
    args: ['-c', 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; cd "$2" && exec node launch.ts', 'sh', String(pid), electronDir],
  };
}
