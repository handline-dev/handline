// The build the app is: the 12-char git sha build.ts stamps into dist/build.json. The daemon reports a git sha too, so the two are
// compared as the same kind of id (app-core's sameBuild).
import fs from 'node:fs';
import path from 'node:path';

/** The sha stamped into `<dir>/build.json`, or null when there is none (a build made before the stamp, or one half written). */
export function readBuildSha(dir: string): string | null {
  try {
    const sha = (JSON.parse(fs.readFileSync(path.join(dir, 'build.json'), 'utf8')) as { sha?: unknown }).sha;
    return typeof sha === 'string' && sha ? sha : null;
  } catch {
    return null;
  }
}
