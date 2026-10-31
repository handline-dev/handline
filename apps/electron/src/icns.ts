// Handline.icns from the 1024 px icon: every size macOS asks for, made with sips and iconutil (Mac only).
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

export const ICON_PNG = path.join(import.meta.dirname, '..', 'assets', 'icon.png');

/** Writes the icon set for `ICON_PNG` to `out`; `scratch` is a folder name that does not exist yet and is removed again. */
export function makeIcns(out: string, scratch: string): void {
  const set = `${scratch}.iconset`;
  fs.mkdirSync(set, { recursive: true });
  try {
    for (const size of [16, 32, 128, 256, 512]) {
      for (const scale of [1, 2]) {
        const name = `icon_${size}x${size}${scale === 2 ? '@2x' : ''}.png`;
        execFileSync('sips', ['-z', String(size * scale), String(size * scale), ICON_PNG, '--out', path.join(set, name)], { stdio: 'ignore' });
      }
    }
    execFileSync('iconutil', ['-c', 'icns', set, '-o', out]);
  } finally {
    fs.rmSync(set, { recursive: true, force: true });
  }
}
