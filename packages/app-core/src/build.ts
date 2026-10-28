// The build the app and the daemon each are, as a git sha: the daemon reports it as `abc123`, `abc123-dirty` or `<version>+abc123` (release).

/** The sha inside a daemon's version. */
export const shaOf = (version: string): string =>
  version
    .split('+')
    .pop()!
    .replace(/-dirty$/, '');

/** Whether a daemon's version is the app's build `sha`; true when either is unknown, as a mismatch cannot be told then. */
export function sameBuild(daemon: string | null | undefined, sha: string | null | undefined): boolean {
  if (!daemon || !sha || daemon === 'unknown') return true;
  return shaOf(daemon).slice(0, 12) === sha.slice(0, 12);
}

/** The line the tray and the top bar show when the daemon is not the app's build; null when they match. */
export function mismatch(daemon: string | null | undefined, app: string | null | undefined): string | null {
  return sameBuild(daemon, app) ? null : `Daemon is ${shaOf(daemon!).slice(0, 7)}, app is ${app!.slice(0, 7)}`;
}
