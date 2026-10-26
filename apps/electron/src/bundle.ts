// What an installed app carries under Contents/Resources: the web build, the daemon and the CLI (electron-builder.config.mjs puts them there).
import path from 'node:path';

export interface Bundle {
  web: string;
  handlined: string;
  hl: string;
}

/** Where the bundle is when the app is packaged; null when it runs from a checkout. */
export function bundleAt(packaged: boolean, resources: string): Bundle | null {
  return packaged
    ? { web: path.join(resources, 'web'), handlined: path.join(resources, 'bin', 'handlined'), hl: path.join(resources, 'bin', 'hl') }
    : null;
}
