// The fake daemon's images: attachment.put takes a file in chunks, attachment.get gives it back, chat.send names the finished ones.
// Image bytes live here only; the replay store (replies, events) holds a digest or the Attachment, never the data.
import { createHash, randomUUID } from 'node:crypto';
import type { Attachment } from '@handline/contract';
import { badRequest, Failure, refused } from './errors.ts';

export const CHUNK = 512 * 1024;
export const MAX_IMAGE = 10 * 1024 * 1024;
export const MAX_PER_MESSAGE = 6;
const MAX_OPEN = 20;
const MAX_UNSENT = 100 * 1024 * 1024;

type Mime = Attachment['mime'];
const MAGIC: [Mime, number[]][] = [
  ['image/png', [0x89, 0x50, 0x4e, 0x47]],
  ['image/jpeg', [0xff, 0xd8, 0xff]],
  ['image/gif', [0x47, 0x49, 0x46, 0x38]],
  ['image/webp', [0x52, 0x49, 0x46, 0x46]],
];
const typeOf = (b: Buffer): Mime | undefined => MAGIC.find(([, sig]) => sig.every((x, i) => b[i] === x))?.[0];

interface Upload {
  name: string;
  mime: Mime;
  bytes: number;
  parts: Buffer[];
  received: number;
}
interface Stored extends Attachment {
  data: Buffer;
  sent: boolean;
}

export class FakeAttachments {
  private uploads = new Map<string, Upload>();
  private files = new Map<string, Stored>();
  /** Ids a test or the daemon's clock has let go of: attachment.get answers not_found. */
  expire(id: string) {
    this.files.delete(id);
  }

  put(p: Record<string, unknown>): { upload: string; received: number; attachment?: Attachment } {
    const { name, mime, bytes, data } = p as { name: string; mime: Mime; bytes: number; data: string };
    const offset = typeof p.offset === 'number' ? p.offset : 0;
    if (!MAGIC.some(([m]) => m === mime)) throw badRequest('Only PNG, JPEG, GIF and WebP images.', 'mime');
    if (!Number.isInteger(bytes) || bytes < 1) throw badRequest('bytes is the whole file size', 'bytes');
    if (bytes > MAX_IMAGE) throw refused('limit', 'An image is at most 10 MiB.');
    const chunk = Buffer.from(data, 'base64');
    if (chunk.length > CHUNK) throw badRequest('A chunk is at most 512 KiB of file.', 'data');
    let id = p.upload as string | undefined;
    let up: Upload | undefined;
    if (id === undefined) {
      if (offset !== 0) throw badRequest('The first chunk starts at 0.', 'offset');
      if (this.uploads.size >= MAX_OPEN) throw refused('limit', 'Too many uploads are open.');
      if (this.unsent() + bytes > MAX_UNSENT) throw refused('limit', 'Too many unsent images.');
      id = randomUUID();
      up = { name: String(name).split(/[\\/]/).pop() || 'image', mime, bytes, parts: [], received: 0 };
      this.uploads.set(id, up);
    } else {
      up = this.uploads.get(id);
      if (!up) throw new Failure('not_found', 'That upload is unknown or expired.');
      if (offset !== up.received) throw new Failure('bad_request', 'offset is not what the daemon holds.', { field: 'offset', received: up.received });
    }
    up.parts.push(chunk);
    up.received += chunk.length;
    if (up.received < up.bytes) return { upload: id, received: up.received };
    this.uploads.delete(id);
    const all = Buffer.concat(up.parts);
    if (all.length !== up.bytes || typeOf(all) !== up.mime) throw badRequest('The file is not the type it says.', 'data');
    const attachment: Attachment = { id: randomUUID(), name: up.name, mime: up.mime, bytes: up.bytes };
    this.files.set(attachment.id, { ...attachment, data: all, sent: false });
    return { upload: id, received: up.received, attachment };
  }

  /** The thumbnail is the original here (no image library): tests use images small enough for one reply. */
  get(p: Record<string, unknown>) {
    const f = this.files.get(String(p.id));
    if (!f) throw new Failure('not_found', 'That image is gone.');
    const thumb = p.thumb === true;
    const offset = thumb ? 0 : typeof p.offset === 'number' ? p.offset : 0;
    const end = Math.min(f.data.length, offset + (thumb ? 256 * 1024 : CHUNK));
    return { mime: f.mime, bytes: f.data.length, offset, data: f.data.subarray(offset, end).toString('base64'), done: end >= f.data.length };
  }

  /** The images chat.send names: each must be finished and not sent before. They are marked sent and given back as Attachments. */
  take(ids: unknown): Attachment[] {
    if (ids === undefined) return [];
    if (!Array.isArray(ids) || ids.length > MAX_PER_MESSAGE) throw badRequest('At most 6 images to a message.', 'attachments');
    const found = ids.map((id) => this.files.get(String(id)));
    if (found.some((f) => !f || f.sent)) throw badRequest('An image is unknown, expired or already sent.', 'attachments');
    return found.map((f) => {
      f!.sent = true;
      const { data: _d, sent: _s, ...a } = f!;
      return a;
    });
  }

  private unsent() {
    return [...this.files.values()].filter((f) => !f.sent).reduce((n, f) => n + f.bytes, 0) + [...this.uploads.values()].reduce((n, u) => n + u.bytes, 0);
  }
}

/** A write's params with image data swapped for its digest: what the replay store keeps to tell a retry from a different call. */
export const keptParams = (p: Record<string, unknown>): Record<string, unknown> =>
  typeof p.data === 'string' ? { ...p, data: createHash('sha1').update(p.data).digest('hex') } : p;
