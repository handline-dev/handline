// The Mac bundle an unpackaged run uses: a copy of Electron.app named Handline, one per Electron version (see launch.ts).
import path from 'node:path';

/** The copy for this Electron version: a new version gets its own, so a running app's bundle is never replaced under it. */
export const bundlePath = (dir: string, version: string): string => path.join(dir, `Handline-${version}.app`);

/** The folder a half-made copy is built in, renamed to its place once whole; the process id keeps two first launches apart. */
export const buildPath = (to: string, pid: number): string => `${to}.building-${pid}`;

/**
 * The plist keys that make the menu bar and the dock say Handline. The executable keeps its name: Electron takes an executable that is not called
 * Electron for a packaged app, which then looks for its page and its binaries in Resources.
 */
export const PLIST_NAMES: readonly (readonly [string, string])[] = [
  ['CFBundleName', 'Handline'],
  ['CFBundleDisplayName', 'Handline'],
];

/** The executable of a bundle made by launch.ts. */
export const bundleExe = (to: string): string => path.join(to, 'Contents', 'MacOS', 'Electron');
