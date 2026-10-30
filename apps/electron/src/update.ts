import { createHash } from 'node:crypto';
import fs from 'node:fs';
import type { UpdateFlow } from '#src/update-flow.ts';

/**
 * Something in the page would be lost by a restart: a box is open, a field holds text (the chat compose box, an answer to
 * an agent's question, the Terminal tab's input), or a send is in flight. A send in flight is the chat's optimistic row,
 * which carries a negative `data-id` until the daemon numbers it (a queued message is on the daemon and is not lost), or
 * any element a view marks `data-pending`. Runs in the page.
 */
export function unsent(doc: { querySelectorAll(s: string): ArrayLike<{ value?: string }> }): boolean {
  if (doc.querySelectorAll('dialog[open], [aria-modal="true"]').length) return true;
  for (const f of Array.from(doc.querySelectorAll('textarea, input[type="text"], input:not([type])')))
    if ((f.value ?? '').trim()) return true;
  return doc.querySelectorAll('[data-id^="-"], [data-pending]').length > 0;
}
export const UNSENT_PROBE = `(${unsent.toString()})(document)`;

/** What a build is: the page that names its hashed assets, and the main bundle. Null while a build is half written. */
export function buildId(files: { page: string; main: string }): string | null {
  try {
    return createHash('sha1')
      .update(fs.readFileSync(files.page))
      .update(String(fs.statSync(files.main).mtimeMs))
      .digest('hex');
  } catch {
    return null;
  }
}

export interface UpdatePorts {
  build(): string | null;
  /** True when nothing on screen would be lost by asking. */
  idle(): Promise<boolean>;
  /** Brings the daemon onto the new build, then relaunches. Rejects with the reason, and the app has not relaunched then. */
  restart(): Promise<void>;
}

/** A newer build than the one running is offered once, as the update notice, and only when the app is idle; a put-off one comes back once a day. */
export class Updater {
  private ports: UpdatePorts;
  private flow: UpdateFlow;
  private running: string | null;
  private checking = false;
  constructor(ports: UpdatePorts, flow: UpdateFlow) {
    this.ports = ports;
    this.flow = flow;
    this.running = ports.build();
  }

  async check(): Promise<void> {
    const now = this.ports.build();
    if (!now) return;
    this.running ??= now; // the first build seen when none was readable at launch is the running one
    this.flow.remind();
    if (now === this.running || this.checking || this.flow.view.phase !== 'idle') return; // told already, or being built
    this.checking = true;
    try {
      if (await this.ports.idle()) this.flow.ready(() => void this.go()); // owed otherwise: the next check offers again
    } finally {
      this.checking = false;
    }
  }

  private async go(): Promise<void> {
    try {
      await this.ports.restart();
    } catch (e) {
      this.flow.failed(e instanceof Error ? e.message : String(e), () => void this.go());
    }
  }
}
