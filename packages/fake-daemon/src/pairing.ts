// The pairing codes and device tokens of the fake daemon: `device.pair` issues a code, `device.redeem` trades it for a token.
import crypto from 'node:crypto';
import { badRequest, refused } from './errors.ts';

type Accepted = (said: string, extra: Record<string, unknown>) => unknown;

export class FakePairing {
  private codes = new Set<string>();
  private tokens = new Map<string, { id: string; name: string }>();

  /** What `device.pair` gives the founder: a one-time code to redeem. */
  issueCode(): string {
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // the Go daemon's, so a code looks the same
    const code = Array.from(crypto.randomBytes(12), (b, i) => (i && i % 4 === 0 ? '-' : '') + alphabet[b % alphabet.length]).join('');
    this.codes.add(code);
    return code;
  }

  knows(token: unknown): boolean {
    return typeof token === 'string' && this.tokens.has(token);
  }

  device(token: unknown): { id: string; name: string } | undefined {
    return typeof token === 'string' ? this.tokens.get(token) : undefined;
  }

  pair(accepted: Accepted, now: Date): unknown {
    const code = this.issueCode();
    return accepted('Pairing code made.', {
      code,
      expires_at: new Date(now.getTime() + 300_000).toISOString(),
      fingerprint: 'fa'.repeat(32),
      uri: `handline://pair?${new URLSearchParams({ host: '127.0.0.1:0', fp: 'fa'.repeat(32), code })}`,
    });
  }

  redeem(accepted: Accepted, p: Record<string, unknown>): unknown {
    if (typeof p.code !== 'string') throw badRequest('code is a string', 'code');
    if (typeof p.name !== 'string' || p.name === '') throw badRequest("name is the device's name, a string", 'name');
    if (!this.codes.delete(p.code.trim().toUpperCase())) throw refused('pairing_code', 'that pairing code is not valid');
    const token = 'hld_' + crypto.randomBytes(32).toString('hex'),
      id = 'dev_' + crypto.randomBytes(6).toString('hex');
    this.tokens.set(token, { id, name: p.name });
    return accepted('Paired.', { token, device: id });
  }
}
