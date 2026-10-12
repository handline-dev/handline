import { describe, expect, it } from 'vitest';
import { RpcError } from '@handline/client';
import { CHUNK, forgetAttachment, knownAttachments, rememberAttachment, uploadImage, whyNotImage } from '../src/attachments.ts';

const file = (n: number) => new Blob([new Uint8Array(n)], { type: 'image/png' });
const A = { id: 'a1', name: 'x.png', mime: 'image/png' as const, bytes: 0 };

describe('uploadImage', () => {
  it('sends 512 KiB chunks, each with its own operation id, and ends with the attachment', async () => {
    const calls: Record<string, unknown>[] = [];
    const call = async (_m: string, p: Record<string, unknown>) => {
      calls.push(p);
      const received = ((p.offset as number) ?? 0) + atob(p.data as string).length;
      return { upload: 'u1', received, ...(received >= (p.bytes as number) ? { attachment: A } : {}) };
    };
    const seen: number[] = [];
    const got = await uploadImage(call, file(CHUNK + 10), 'x.png', 'image/png', (s) => seen.push(s));
    expect(got).toBe(A);
    expect(calls.map((c) => [c.offset ?? 0, c.upload])).toEqual([
      [0, undefined],
      [CHUNK, 'u1'],
    ]);
    expect(new Set(calls.map((c) => c.request_id)).size).toBe(2);
    expect(seen).toEqual([CHUNK, CHUNK + 10]);
  });

  it('sends a chunk whose reply was lost again under the same operation id, and gives up after three tries', async () => {
    const ids: unknown[] = [];
    let n = 0;
    const flaky = async (_m: string, p: Record<string, unknown>) => {
      ids.push(p.request_id);
      if (++n < 3) throw new RpcError('transport', 'lost');
      return { upload: 'u', received: 5, attachment: A };
    };
    await uploadImage(flaky, file(5), 'x.png', 'image/png');
    expect(new Set(ids).size).toBe(1);
    const dead = async () => {
      throw new RpcError('transport', 'lost');
    };
    await expect(uploadImage(dead, file(5), 'x.png', 'image/png')).rejects.toMatchObject({ code: 'transport' });
  });

  it('follows the daemon when it holds a different length, and stops on a refusal', async () => {
    const offsets: unknown[] = [];
    let first = true;
    const call = async (_m: string, p: Record<string, unknown>) => {
      offsets.push(p.offset ?? 0);
      if (first) {
        first = false;
        return { upload: 'u', received: 3 };
      }
      if (p.offset === 3 && offsets.length === 2) throw new RpcError('bad_request', 'offset', { field: 'offset', received: 4 });
      return { upload: 'u', received: 10, attachment: A };
    };
    await uploadImage(call, file(10), 'x.png', 'image/png');
    expect(offsets).toEqual([0, 3, 4]);
    const limit = async () => {
      throw new RpcError('refused', 'too many', { gate: 'limit', reason: 'Too many unsent images.' });
    };
    await expect(uploadImage(limit, file(5), 'x.png', 'image/png')).rejects.toMatchObject({ gate: 'limit' });
  });
});

describe('an upload that is cancelled', () => {
  it('sends nothing more, not even a retry of the chunk in flight', async () => {
    const signal = { aborted: false };
    let calls = 0;
    const call = async () => {
      calls += 1;
      signal.aborted = true;
      throw new RpcError('transport', 'lost');
    };
    await expect(uploadImage(call, file(5), 'x.png', 'image/png', undefined, signal)).rejects.toThrow('cancelled');
    expect(calls).toBe(1);
  });
});

describe('the images a send shows before the daemon answers', () => {
  it('are handed over once, and not at all after they were forgotten', () => {
    rememberAttachment(A);
    expect(knownAttachments(['a1'])).toEqual([A]);
    expect(knownAttachments(['a1'])).toEqual([]);
    rememberAttachment(A);
    forgetAttachment('a1');
    expect(knownAttachments(['a1'])).toEqual([]);
  });
});

describe('whyNotImage', () => {
  it('lets PNG, JPEG, GIF and WebP up to 10 MB through, and says why not otherwise', () => {
    expect(whyNotImage({ type: 'image/webp', size: 10, name: 'a' })).toBeNull();
    expect(whyNotImage({ type: 'image/svg+xml', size: 10, name: 'a.svg' })).toContain('only PNG');
    expect(whyNotImage({ type: 'image/png', size: 11 * 1024 * 1024, name: 'big.png' })).toContain('10 MB');
    expect(whyNotImage({ type: 'image/png', size: 0, name: 'e.png' })).toContain('empty');
  });
});
