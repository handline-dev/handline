// `pnpm start` and `pnpm app`: runs the shell. On a Mac, an unpackaged Electron is named "Electron" in the menu bar and the dock, because the name
// comes from the bundle's Info.plist; so the run uses a copy of Electron.app named Handline, one per Electron version, kept beside the data
// (HANDLINE_BUNDLE_DIR moves them). If the copy cannot be made the run uses plain Electron and says so.
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { buildPath, bundleExe, bundlePath, PLIST_NAMES } from '#src/handline-bundle.ts';
import { makeIcns } from '#src/icns.ts';

const require = createRequire(import.meta.url);
const electron = require('electron') as string; // the path of the executable inside Electron.app

/** Electron.app copied as Handline-<version>.app, once; returns its executable. A finished copy is never touched, so a running app keeps its own. */
function handlineBundle(from: string, to: string): string {
  const exe = bundleExe(to);
  if (fs.existsSync(exe)) return exe;
  fs.rmSync(to, { recursive: true, force: true }); // a bundle without its executable is one an older launcher made, with the executable renamed
  const work = buildPath(to, process.pid);
  fs.rmSync(work, { recursive: true, force: true });
  try {
    fs.mkdirSync(path.dirname(to), { recursive: true });
    execFileSync('cp', ['-Rc', from, work]); // an APFS clone: no second 750 MB
    const plist = path.join(work, 'Contents', 'Info.plist');
    for (const [key, value] of PLIST_NAMES) execFileSync('plutil', ['-replace', key, '-string', value, plist]);
    makeIcns(path.join(work, 'Contents', 'Resources', 'electron.icns'), work); // the file the copy's Info.plist names: Finder, the Dock and Cmd-Tab show Handline's mark
    // Editing the plist breaks the bundle's seal, and Apple Silicon wants a signature: the outer seal only, the frameworks keep their own.
    execFileSync('codesign', ['--force', '--sign', '-', work]);
    fs.renameSync(work, to);
  } finally {
    fs.rmSync(work, { recursive: true, force: true }); // gone after the rename; what a failure left otherwise
  }
  return exe;
}

let exe = electron;
if (os.platform() === 'darwin') {
  try {
    const version = (require('electron/package.json') as { version: string }).version;
    const dir = process.env.HANDLINE_BUNDLE_DIR ?? path.join(os.homedir(), '.local', 'share', 'handline');
    exe = handlineBundle(path.resolve(electron, '../../..'), bundlePath(dir, version));
  } catch (e) {
    console.warn(`Could not make the Handline bundle, so the menu bar says Electron: ${e instanceof Error ? e.message : String(e)}`);
  }
}
const child = spawn(exe, ['.', ...process.argv.slice(2)], { cwd: import.meta.dirname, stdio: 'inherit' });
for (const s of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) process.once(s, () => child.kill(s));
child.once('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
