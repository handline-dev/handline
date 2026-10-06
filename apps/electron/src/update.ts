import { createHash } from 'node:crypto';
import fs from 'node:fs';

/** Something in the page would be lost by a restart: a box is open, a field holds text, or a send has not landed. Runs in the page. */
export function unsent(doc: { querySelectorAll(s: string): ArrayLike<{ value?: string }> }): boolean {
  if (doc.querySelectorAll('dialog[open], [aria-modal="true"]').length) return true;
  for (const f of Array.from(doc.querySelectorAll('textarea, input[type="text"], input:not([type])'))) if ((f.value ?? '').trim()) return true;
  return doc.querySelectorAll('[data-landing="queued"], [data-landing="sending"]').length > 0;
}
export const UNSENT_PROBE = `(${unsent.toString()})(document)`;

/** What a build is: the page that names its hashed assets, and the main bundle. Null while a build is half written. */
export function buildId(files: { page: string; main: string }): string | null {
  try {
    return createHash('sha1').update(fs.readFileSync(files.page)).update(String(fs.statSync(files.main).mtimeMs)).digest('hex');
  } catch { return null; }
}

export interface UpdatePorts {
  build(): string | null;
  /** True when nothing on screen would be lost by asking. */
  idle(): Promise<boolean>;
  ask(): Promise<boolean>;
  restart(): void;
}

/** A newer build than the one running is owed a question, asked only when idle, and a build said no to is not asked about again. */
export class Updater {
  private ports: UpdatePorts;
  private running: string | null;
  private declined: string | null = null;
  private asking = false;
  constructor(ports: UpdatePorts) { this.ports = ports; this.running = ports.build(); }

  async check(): Promise<void> {
    if (this.asking) return;
    const now = this.ports.build();
    if (!now) return;
    this.running ??= now; // the first build seen when none was readable at launch is the running one
    if (now === this.running || now === this.declined) return;
    this.asking = true;
    try {
      if (!(await this.ports.idle())) return; // owed: the next check asks again
      if (await this.ports.ask()) this.ports.restart();
      else this.declined = now;
    } finally { this.asking = false; }
  }
}
