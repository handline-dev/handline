// `pnpm start` and `pnpm app`: runs the shell. On a Mac, an unpackaged Electron is named "Electron" in the menu bar and the dock, because the name
// comes from the bundle's Info.plist; so the run uses a copy of Electron.app named Handline, kept beside the data (HANDLINE_BUNDLE_DIR moves it).
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const electron = require('electron') as string; // the path of the executable inside Electron.app

/** Electron.app copied to `to` as Handline.app, once per Electron version; returns its executable. */
function handlineBundle(from: string, to: string, version: string): string {
  const exe = path.join(to, 'Contents', 'MacOS', 'Handline');
  const stamp = path.join(to, 'Contents', '.electron-version');
  if (fs.existsSync(stamp) && fs.readFileSync(stamp, 'utf8') === version && fs.existsSync(exe)) return exe;
  fs.rmSync(to, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(to), { recursive: true });
  execFileSync('cp', ['-Rc', from, to]); // an APFS clone: no second 750 MB
  fs.renameSync(path.join(to, 'Contents', 'MacOS', 'Electron'), exe);
  const plist = path.join(to, 'Contents', 'Info.plist');
  for (const [key, value] of [
    ['CFBundleName', 'Handline'],
    ['CFBundleDisplayName', 'Handline'],
    ['CFBundleExecutable', 'Handline'],
  ])
    execFileSync('plutil', ['-replace', key, '-string', value, plist]);
  // Editing the plist breaks the bundle's seal, and Apple Silicon wants a signature.
  execFileSync('codesign', ['--force', '--sign', '-', to]); // the outer seal only: the frameworks inside keep their own signatures
  fs.writeFileSync(stamp, version);
  return exe;
}

let exe = electron;
if (os.platform() === 'darwin') {
  const from = path.resolve(electron, '../../..');
  const version = (require('electron/package.json') as { version: string }).version;
  const to = process.env.HANDLINE_BUNDLE_DIR ?? path.join(os.homedir(), '.local', 'share', 'handline', 'Handline.app');
  exe = handlineBundle(from, to, version);
}
const child = spawn(exe, ['.', ...process.argv.slice(2)], { cwd: import.meta.dirname, stdio: 'inherit' });
for (const s of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) process.once(s, () => child.kill(s));
child.once('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
