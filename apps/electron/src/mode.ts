// The app's two modes. Connect (the default) attaches to a daemon that is already running here, or pairs with another device's gateway,
// and never starts or stops a daemon. Own daemon starts the daemon and gateway itself and stops them on quit (owner.ts). The choice and the
// remote pairing live in one small file in the app's data folder, readable by this user only (it holds a device token).
import fs from 'node:fs';
import path from 'node:path';
import { isTailscaleHost, listenerUrl, parsePairingUri, redeemPairing, type PairingOffer, type Transport } from '@handline/client';

export type Mode = 'connect' | 'own';

/** Another device's gateway this app paired with: where it is, which certificate to trust (null: plain Tailscale), our token there. */
export interface Remote {
  host: string;
  fingerprint: string | null;
  token: string;
  device: string;
}

export interface Settings {
  mode: Mode;
  remote: Remote | null;
}

export const parseMode = (v: unknown): Mode | undefined => (v === 'connect' || v === 'own' ? v : undefined);

const isRemote = (v: unknown): v is Remote => {
  const r = v as Partial<Remote> | null;
  return (
    !!r &&
    typeof r.host === 'string' &&
    typeof r.token === 'string' &&
    typeof r.device === 'string' &&
    (r.fingerprint === null || typeof r.fingerprint === 'string')
  );
};

export class SettingsFile {
  private file: string;
  constructor(file: string) {
    this.file = file;
  }
  /** What is kept, or the defaults; `forced` (HANDLINE_MODE) wins over the kept mode without changing the file. */
  read(forced?: Mode): Settings {
    let kept: Partial<Settings> = {};
    try {
      kept = JSON.parse(fs.readFileSync(this.file, 'utf8')) as Partial<Settings>;
    } catch {
      /* none yet */
    }
    return { mode: forced ?? parseMode(kept.mode) ?? 'connect', remote: isRemote(kept.remote) ? kept.remote : null };
  }
  write(s: Settings): void {
    fs.mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    fs.writeFileSync(this.file, JSON.stringify(s), { mode: 0o600 });
  }
}

/** What the person gave for another device: the `handline://` link `hl device pair` prints, or a Tailscale address and a code. */
export interface OfferInput {
  link?: string;
  address?: string;
  code?: string;
}

export function offerFrom(i: OfferInput): PairingOffer {
  const link = i.link?.trim();
  if (link) return parsePairingUri(link);
  const address = i.address
    ?.trim()
    .replace(/^[a-z]+:\/\//, '')
    .replace(/\/.*$/, '');
  const code = i.code?.trim();
  if (!address || !code) throw new Error('give the pairing link, or the address and the code');
  const host = /:\d+$/.test(address) ? address : `${address}:7443`;
  // Without a certificate fingerprint only a Tailscale name can be trusted; parsePairingUri says so for anything else.
  return parsePairingUri(`handline://pair?host=${encodeURIComponent(host)}&code=${encodeURIComponent(code)}`);
}

export const cannotPair = (host: string, why: string): string => `the gateway at ${host} cannot be paired with: ${why}`;

/** Opens the socket to a listener: pinned wss for a certificate fingerprint, plain ws for a Tailscale name. */
export type Open = (url: string, fingerprint: string | null) => Promise<Transport>;

const withTimeout = <T>(p: Promise<T>, ms: number, what: string): Promise<T> =>
  new Promise((ok, fail) => {
    const t = setTimeout(() => fail(new Error(what)), ms);
    p.then(
      (v) => (clearTimeout(t), ok(v)),
      (e) => (clearTimeout(t), fail(e)),
    );
  });

/** Pairs with another device's gateway the way the phone does and returns what to keep. A gateway that cannot be paired with says why and never hangs. */
export async function pairRemote(input: OfferInput, d: { open: Open; name: string; timeoutMs?: number }): Promise<Remote> {
  const offer = offerFrom(input); // a bad link or an untrusted address throws its own sentence
  const ms = d.timeoutMs ?? 10_000;
  const connect = () =>
    withTimeout(
      d.open(listenerUrl(offer.host, offer.fingerprint !== null), offer.fingerprint),
      ms,
      `nothing answered in ${Math.round(ms / 1000)} s`,
    );
  try {
    const { token, device } = await redeemPairing(connect, offer.code, d.name, ms);
    return { host: offer.host, fingerprint: offer.fingerprint, token, device };
  } catch (e) {
    const why = e instanceof Error ? e.message : String(e);
    // The upgrade refusal means something answered that is not a Handline gateway.
    throw new Error(cannotPair(offer.host, /did not accept the WebSocket upgrade/.test(why) ? 'it is not a Handline gateway' : why), {
      cause: e,
    });
  }
}

/** Where the window reaches the daemon: the remote's address, or this machine's gateway. */
export const remoteUrl = (r: Remote): string => listenerUrl(r.host, r.fingerprint !== null);

/** Plain ws to a remote Tailscale name is the one case the page needs told it is allowed. */
export const plainRemoteHost = (r: Remote): string | null =>
  r.fingerprint === null && isTailscaleHost(r.host) ? r.host.replace(/:\d+$/, '') : null;

export type LinkStatus = 'connecting' | 'connected' | 'no-daemon' | 'cannot-pair';

/** What the tray says in connect mode: which daemon (address, version) and whether it answers. */
export class Link {
  status: LinkStatus = 'connecting';
  address: string | null = null;
  version: string | null = null;
  reason = '';
  private listeners: (() => void)[] = [];
  onChange(f: () => void): void {
    this.listeners.push(f);
  }
  set(status: LinkStatus, p: { address?: string | null; reason?: string } = {}): void {
    this.status = status;
    if (p.address !== undefined) this.address = p.address;
    this.reason = p.reason ?? '';
    for (const f of this.listeners) f();
  }
  /** The daemon's version: the loaded git sha its hello gives, short. */
  setVersion(daemon: string | undefined): void {
    this.version = daemon ? daemon.slice(0, 7) : null;
    for (const f of this.listeners) f();
  }
}
