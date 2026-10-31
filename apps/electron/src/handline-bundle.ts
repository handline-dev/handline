// The Mac bundle an unpackaged run uses: a copy of Electron.app named Handline, one per Electron version (see launch.ts).
import path from 'node:path';

/** The copy for this Electron version: a new version gets its own, so a running app's bundle is never replaced under it. */
export const bundlePath = (dir: string, version: string): string => path.join(dir, `Handline-${version}.app`);

/** The folder a half-made copy is built in, renamed to its place once whole; the process id keeps two first launches apart. */
export const buildPath = (to: string, pid: number): string => `${to}.building-${pid}`;

/** The plist keys that make the menu bar, the dock and the Activity Monitor say Handline. */
export const PLIST_NAMES: readonly (readonly [string, string])[] = [
  ['CFBundleName', 'Handline'],
  ['CFBundleDisplayName', 'Handline'],
  ['CFBundleExecutable', 'Handline'],
];
