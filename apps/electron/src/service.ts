// The daemon is a launchd service (dev.orcapipe.daemon), not a child of this app. The app makes sure it runs, the way `hl` does
// (handline/infra/daemonclient.py ensure_daemon): kickstart it, and when launchd has not loaded it, load the installed plist first.
// It never spawns a daemon and never stops one except from the tray's explicit item.

export const SERVICE_LABEL = 'dev.orcapipe.daemon';

export type ServiceState = 'starting' | 'running' | 'stopped' | 'failed';

export interface ServiceDeps {
  /** `launchctl <args>`; resolves with its exit code and output. */
  launchctl(args: string[]): Promise<{ code: number; out: string }>;
  /** Whether the daemon's socket and the gateway's local socket both answer. */
  up(): Promise<boolean>;
  /** Whether the service's plist is installed (`hl service install`). */
  installed(): boolean;
  /** The launchd domain, `gui/<uid>`, and the plist path. */
  domain: string;
  plist: string;
  wait(ms: number): Promise<void>;
  label?: string;
  /** How many 500 ms polls a started service gets to answer. */
  polls?: number;
}

export type Ensured = { ok: true; started: boolean } | { ok: false; reason: string };

const target = (d: ServiceDeps) => `${d.domain}/${d.label ?? SERVICE_LABEL}`;
const said = (r: { out: string }) => r.out.trim().slice(0, 200) || 'no reason given';

/** Attaches to the running service, or starts that same service; the reason when it cannot be made to answer. Never throws. */
export async function ensureService(d: ServiceDeps): Promise<Ensured> {
  if (await d.up()) return { ok: true, started: false };
  if (!d.installed()) return { ok: false, reason: 'the daemon service is not installed (run `hl service install`)' };
  let r = await d.launchctl(['kickstart', target(d)]);
  if (r.code !== 0 && /could not find service/i.test(r.out)) {
    // Installed and not loaded (a bootout, a login that did not load it): load what is installed, then start it.
    const loaded = await d.launchctl(['bootstrap', d.domain, d.plist]);
    if (loaded.code !== 0) return { ok: false, reason: `launchd would not load the service: ${said(loaded)}` };
    r = await d.launchctl(['kickstart', target(d)]);
  }
  if (r.code !== 0) return { ok: false, reason: `launchd would not start the service: ${said(r)}` };
  for (let i = 0; i < (d.polls ?? 60); i++) {
    if (await d.up()) return { ok: true, started: true };
    await d.wait(500);
  }
  return { ok: false, reason: 'launchd started the service but it did not answer in 30 s' };
}

/** Stops the service until the next launch of the app or `hl` starts it; only the tray's confirmed item calls this. */
export async function stopService(d: ServiceDeps): Promise<{ ok: true } | { ok: false; reason: string }> {
  const r = await d.launchctl(['bootout', target(d)]);
  return r.code === 0 || /could not find service|no such process/i.test(r.out) ? { ok: true } : { ok: false, reason: said(r) };
}

/** The service's state as the tray and the page show it. */
export class ServiceStatus {
  state: ServiceState = 'stopped';
  /** What the tray shows next to a failure. */
  reason = '';
  private listeners: (() => void)[] = [];
  onChange(f: () => void): void {
    this.listeners.push(f);
  }
  set(state: ServiceState, reason = ''): void {
    this.state = state;
    this.reason = reason;
    for (const f of this.listeners) f();
  }
}
