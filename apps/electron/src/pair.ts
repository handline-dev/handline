// The app pairs itself: it is the founder's own process, so it proves the founder key to the gateway's local socket, asks for a code,
// and trades the code for a device token on a second connection. The key is read here, used for those two calls and dropped: it is
// never logged, never handed to the page and never stored again. Only the device token is kept.
import fs from 'node:fs';
import path from 'node:path';
import { PROTOCOL, type Transport } from '@handline/client';

/** The founder's key file in the protected store (handline/infra/protected.py: one folder per home, named by its path). */
/** The daemon names the folder by the home's real path (a symlinked temp folder included). */
const realpath = (p: string): string => {
  try {
    return fs.realpathSync(p);
  } catch {
    return path.resolve(p);
  }
};

export function founderKeyFile(userHome: string, home: string): string {
  return path.join(userHome, '.config', 'orcapipe', 'protected', realpath(home).replace(/[^A-Za-z0-9]/g, '-'), 'founder-keys', 'key');
}

export interface PairDeps {
  /** The founder key, or null while there is none (the daemon has not run yet). */
  readKey(): string | null;
  /** A connection to the gateway's local socket. */
  open(): Promise<Transport>;
  name: string;
}

export interface Kept {
  token: string;
  device: string;
}

export class NoFounderKey extends Error {
  constructor() {
    super('the founder key is not there yet');
  }
}

interface Reply {
  result?: Record<string, unknown>;
  error?: { message: string; data?: { reason?: string } };
}

/** One call on a fresh connection, then it closes. */
async function once(open: () => Promise<Transport>, calls: [string, Record<string, unknown>][]): Promise<Reply> {
  const t = await open();
  try {
    let last: Reply = {};
    let id = 0;
    for (const [method, params] of calls) {
      last = await new Promise<Reply>((ok, fail) => {
        const timer = setTimeout(() => fail(new Error(`${method}: the gateway did not answer`)), 10_000);
        t.onmessage = (m) => {
          clearTimeout(timer);
          ok(JSON.parse(m) as Reply);
        };
        t.onclose = () => {
          clearTimeout(timer);
          fail(new Error(`${method}: the gateway closed the connection`));
        };
        t.send(JSON.stringify({ jsonrpc: '2.0', id: ++id, method, params }));
      });
      if (last.error) throw new Error(`${method}: ${last.error.data?.reason ?? last.error.message}`);
    }
    return last;
  } finally {
    t.onclose = null;
    t.close();
  }
}

const rid = () => `app-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

/** Pairs this computer and returns its device token. Throws NoFounderKey when the daemon has not made the key yet. */
export async function pairSelf(d: PairDeps): Promise<Kept> {
  const key = d.readKey();
  if (!key) throw new NoFounderKey();
  const paired = await once(d.open, [
    ['hello', { client: 'electron-pair', protocol: PROTOCOL, key }],
    ['device.pair', { name: d.name, request_id: rid() }],
  ]);
  const code = paired.result?.code;
  if (typeof code !== 'string') throw new Error('device.pair gave no code');
  const redeemed = await once(d.open, [['device.redeem', { code, name: d.name, request_id: rid() }]]);
  const { token, device } = redeemed.result ?? {};
  if (typeof token !== 'string' || typeof device !== 'string') throw new Error('device.redeem gave no token');
  return { token, device };
}

/** Whether the gateway still lists this device (a revoked one is gone). Null when it could not ask. */
export async function stillPaired(d: PairDeps, device: string): Promise<boolean | null> {
  const key = d.readKey();
  if (!key) return null;
  try {
    const r = await once(d.open, [
      ['hello', { client: 'electron-pair', protocol: PROTOCOL, key }],
      ['devices.list', {}],
    ]);
    const list = r.result?.devices;
    return Array.isArray(list) ? list.some((x) => (x as { id?: string }).id === device) : null;
  } catch {
    return null;
  }
}

/** The kept token: one small file in the app's own data folder, readable by this user only. */
export class TokenFile {
  private file: string;
  constructor(file: string) {
    this.file = file;
  }
  read(): Kept | null {
    try {
      const v = JSON.parse(fs.readFileSync(this.file, 'utf8')) as Partial<Kept>;
      return typeof v.token === 'string' && typeof v.device === 'string' ? { token: v.token, device: v.device } : null;
    } catch {
      return null;
    }
  }
  write(k: Kept): void {
    fs.mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    fs.writeFileSync(this.file, JSON.stringify(k), { mode: 0o600 });
  }
}

/** The token to hand the page: the kept one while the gateway still lists it, else a new pairing. Retries while the daemon makes its key. */
export async function ensureToken(d: PairDeps, file: TokenFile, wait: (ms: number) => Promise<void>, tries = 30): Promise<string> {
  const kept = file.read();
  if (kept && (await stillPaired(d, kept.device)) !== false) return kept.token;
  for (let i = 1; ; i++) {
    try {
      const k = await pairSelf(d);
      file.write(k);
      return k.token;
    } catch (e) {
      if (!(e instanceof NoFounderKey) || i >= tries) throw e;
      await wait(1000);
    }
  }
}
