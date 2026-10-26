// The `hl` command an installed app carries: whether it is on the person's PATH, and the link that puts it there.
import fs from 'node:fs';
import path from 'node:path';

export interface CliState {
  /** The app carries an `hl` (it is an installed app). */
  bundled: boolean;
  /** `dir/hl` is a link to the bundled one. */
  linked: boolean;
  /** Where the link goes. */
  link: string;
}

export function cliState(bundled: string | null, dir: string): CliState {
  const link = path.join(dir, 'hl');
  let target = '';
  try {
    target = fs.readlinkSync(link);
  } catch {
    /* no link, or a real file */
  }
  return { bundled: !!bundled, linked: !!bundled && target === bundled, link };
}

/** Links `dir/hl` to the bundled `hl`. Another file there is never replaced: it throws with a sentence saying so. */
export function linkCli(bundled: string, dir: string): void {
  const link = path.join(dir, 'hl');
  fs.mkdirSync(dir, { recursive: true });
  try {
    if (fs.lstatSync(link).isSymbolicLink()) fs.unlinkSync(link);
    else throw new Error(`${link} is already a file: remove it first`);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e;
  }
  fs.symlinkSync(bundled, link);
}
