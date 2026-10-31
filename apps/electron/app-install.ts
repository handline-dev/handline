// `pnpm app:install`: writes /Applications/Handline.app, a thin launcher that does what `pnpm app` does with no terminal (src/launcher-app.ts).
// HANDLINE_APPLICATIONS_DIR puts it somewhere else (a try-out). Run it again to replace the launcher.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { infoPlist, launcherScript } from '#src/launcher-app.ts';

const dir = process.env.HANDLINE_APPLICATIONS_DIR ?? '/Applications';
const target = path.join(dir, 'Handline.app');
const work = `${target}.installing-${process.pid}`;
const icon = path.join(import.meta.dirname, 'assets', 'icon.png');

/** Handline.icns from the 1024 px icon: every size macOS asks for, made with sips and iconutil. */
function makeIcns(out: string): void {
  const set = `${work}.iconset`;
  fs.mkdirSync(set, { recursive: true });
  try {
    for (const size of [16, 32, 128, 256, 512]) {
      for (const scale of [1, 2]) {
        const name = `icon_${size}x${size}${scale === 2 ? '@2x' : ''}.png`;
        execFileSync('sips', ['-z', String(size * scale), String(size * scale), icon, '--out', path.join(set, name)], { stdio: 'ignore' });
      }
    }
    execFileSync('iconutil', ['-c', 'icns', set, '-o', out]);
  } finally {
    fs.rmSync(set, { recursive: true, force: true });
  }
}

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
  makeIcns(path.join(work, 'Contents', 'Resources', 'Handline.icns'));
  execFileSync('codesign', ['--force', '--sign', '-', work]);
  fs.rmSync(target, { recursive: true, force: true });
  fs.renameSync(work, target);
} finally {
  fs.rmSync(work, { recursive: true, force: true });
}
console.log(
  `${target} is in place. Open it from Applications or pin it to the Dock; it starts Handline from ~/.local/share/handline/app-main (run pnpm app once first if that folder is missing).`,
);
