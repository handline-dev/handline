// The installed app's own update: it reads latest-mac.yml at the address the build was made for (the rolling release of .github/workflows/mac-release.yml),
// downloads the newer zip, checks it, prepares the new Handline.app beside the old one and offers it as the one update notice (update-flow.ts).
// "Restart now" swaps the two (swap.ts) and starts the new one. This is the whole of what an updater does here, behind PkgUpdater, so that
// electron-updater can take its place once a Developer ID is there (Squirrel.Mac refuses an ad-hoc signed update).
import type { UpdateFlow } from '#src/update-flow.ts';

export interface PkgUpdater {
  check(): Promise<void>;
}

export interface Latest {
  version: string;
  /** The zip, relative to the address the yml is at. */
  path: string;
  /** Base64, as electron-builder writes it. */
  sha512: string;
}

/** The three top-level keys of latest-mac.yml that matter (the file is flat enough that a YAML parser would only be weight). Null when one is missing. */
export function parseLatest(yml: string): Latest | null {
  const top = (key: string) => new RegExp(`^${key}:\\s*(.+?)\\s*$`, 'm').exec(yml)?.[1]?.replace(/^['"]|['"]$/g, '');
  const version = top('version');
  const path = top('path');
  const sha512 = top('sha512');
  return version && path && sha512 ? { version, path, sha512 } : null;
}

/** The `url:` of the app-update.yml electron-builder puts in Resources when a publish address is set. */
export function updateUrl(yml: string): string | null {
  return /^url:\s*(\S+)\s*$/m.exec(yml)?.[1]?.replace(/^['"]|['"]$/g, '') ?? null;
}

const nums = (v: string) => (v.split('-')[0] ?? '').split('.').map((n) => Number(n) || 0);

/** Whether `candidate` is a later version than `current`; a pre-release suffix ("0.0.0-dev") is earlier than the same numbers without one. */
export function isNewer(candidate: string, current: string): boolean {
  const a = nums(candidate);
  const b = nums(current);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d !== 0) return d > 0;
  }
  return current.includes('-') && !candidate.includes('-');
}

export interface PkgPorts {
  /** The version of the app that runs. */
  current(): string;
  text(url: string): Promise<string>;
  /** Saves the file; `progress` is called with 0 to 1. */
  download(url: string, progress: (fraction: number) => void): Promise<string>;
  sha512(file: string): Promise<string>;
  /** Unpacks the zip beside the installed app and returns the new Handline.app's path. */
  stage(zip: string): Promise<string>;
  /** True when nothing on screen would be lost by asking. */
  idle(): Promise<boolean>;
  /** Puts the staged app in place of the installed one, moves the daemon onto it and starts it. Rejects with the reason, and the old app is still in place then. */
  swapAndRestart(staged: string): Promise<void>;
}

/** Offers a newer build once, when the app is idle: downloaded, checked and unpacked before the person is asked. */
export class SelfUpdater implements PkgUpdater {
  private busy = false;
  private ports: PkgPorts;
  private flow: UpdateFlow;
  private base: string;
  constructor(ports: PkgPorts, flow: UpdateFlow, base: string) {
    this.ports = ports;
    this.flow = flow;
    this.base = base.replace(/\/+$/, '');
  }

  async check(): Promise<void> {
    this.flow.remind();
    if (this.busy || this.flow.view.phase !== 'idle') return; // told already, or being prepared
    this.busy = true;
    try {
      const latest = parseLatest(await this.ports.text(`${this.base}/latest-mac.yml`));
      if (!latest || !isNewer(latest.version, this.ports.current())) return;
      if (!(await this.ports.idle())) return; // owed: the next check offers again
      await this.prepare(latest);
    } catch {
      /* offline, or the release is being replaced: the next check tries again */
    } finally {
      this.busy = false;
    }
  }

  private async prepare(latest: Latest): Promise<void> {
    try {
      this.flow.preparing('Downloading', 0);
      const zip = await this.ports.download(`${this.base}/${latest.path}`, (f) => this.flow.preparing('Downloading', f));
      this.flow.preparing('Checking', 1);
      if ((await this.ports.sha512(zip)) !== latest.sha512) throw new Error('the download does not match its checksum');
      const staged = await this.ports.stage(zip);
      this.flow.ready(() => void this.go(staged));
    } catch (e) {
      this.flow.failed(e instanceof Error ? e.message : String(e), () => void this.retry(latest));
    }
  }

  private async retry(latest: Latest): Promise<void> {
    this.flow.idle();
    await this.prepare(latest);
  }

  private async go(staged: string): Promise<void> {
    try {
      await this.ports.swapAndRestart(staged);
    } catch (e) {
      this.flow.failed(e instanceof Error ? e.message : String(e), () => void this.go(staged));
    }
  }
}
