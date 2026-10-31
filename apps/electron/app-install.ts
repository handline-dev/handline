// `pnpm app:install`: writes /Applications/Handline.app, a thin launcher that does what `pnpm app` does with no terminal (src/launcher-app.ts).
// HANDLINE_APPLICATIONS_DIR puts it somewhere else (a try-out). Run it again to replace the launcher.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { makeIcns } from '#src/icns.ts';
import { infoPlist, launcherScript } from '#src/launcher-app.ts';

const dir = process.env.HANDLINE_APPLICATIONS_DIR ?? '/Applications';
const target = path.join(dir, 'Handline.app');
const work = `${target}.installing-${process.pid}`;

if (os.platform() !== 'darwin') {
  console.error('app:install makes a Mac app: it runs on a Mac only');
  process.exit(1);
}
fs.rmSync(work, { recursive: true, force: true });
try {
  fs.mkdirSync(path.join(work, 'Contents', 'MacOS'), { recursive: true });
  fs.mkdirSync(path.join(work, 'Contents', 'Resources'), { recursive: true });
  fs.writeFileSync(path.join(work, 'Contents', 'Info.plist'), infoPlist());
  fs.writeFileSync(path.join(work, 'Contents', 'MacOS', 'Handline'), launcherScript(), { mode: 0o755 });
  makeIcns(path.join(work, 'Contents', 'Resources', 'Handline.icns'), work);
  execFileSync('codesign', ['--force', '--sign', '-', work]);
  fs.rmSync(target, { recursive: true, force: true });
  fs.renameSync(work, target);
} finally {
  fs.rmSync(work, { recursive: true, force: true });
}
console.log(
  `${target} is in place. Open it from Applications or pin it to the Dock; it starts Handline from ~/.local/share/handline/app-main (run pnpm app once first if that folder is missing).`,
);
