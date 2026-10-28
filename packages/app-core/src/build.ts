// The build the app and the daemon each are, as a git sha: the daemon reports it as `abc123`, `abc123-dirty` or `<version>+abc123` (release).

/** The sha inside a daemon's version. */
export const shaOf = (version: string): string =>
  version
    .split('+')
    .pop()!
    .replace(/-dirty$/, '');

/** Two shas are the same build when one is the other cut short (the daemon's own stamp can be 9 or 12 characters, the app's 12). */
export const samePrefix = (a: string, b: string): boolean => Math.min(a.length, b.length) >= 7 && (a.startsWith(b) || b.startsWith(a));

/** Whether a daemon's version is the app's build `sha`; true when either is unknown, as a mismatch cannot be told then. */
export function sameBuild(daemon: string | null | undefined, sha: string | null | undefined): boolean {
  if (!daemon || !sha || daemon === 'unknown') return true;
  return samePrefix(shaOf(daemon), sha);
}

/** The line the tray and the top bar show when the daemon is not the app's build; null when they match. */
export function mismatch(daemon: string | null | undefined, app: string | null | undefined): string | null {
  return sameBuild(daemon, app) ? null : `Daemon is ${shaOf(daemon!).slice(0, 7)}, app is ${app!.slice(0, 7)}`;
}
