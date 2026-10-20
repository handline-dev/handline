// The fake daemon's images: attachment.put takes a file in chunks, attachment.get gives it back, a message or a task's text names the finished ones.
// Image bytes live here only; the replay store (replies, events) holds a digest or the Attachment, never the data.
import { createHash, randomBytes } from 'node:crypto';
import type { Attachment, ImagePart, MemoFile, TextPart } from '@handline/contract';
import { badRequest, Failure, invalidParams, refused } from './errors.ts';

// These mirror the contract's x-attachments limits (kept in prose there; app-data holds the client's copy).
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
const newId = () => randomBytes(8).toString('hex');

/** The ids a text names, in order: `![caption](attachment:ID)` and nothing else (x-attachments, texts). */
export const refs = (text: string): string[] => [...text.matchAll(/!\[[^\]]*\]\(attachment:([^)]*)\)/g)].map((m) => m[1]!);

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

  /** A file a memo carries (an image or Markdown), kept as the daemon keeps one; a test passes the returned id as task.comment.add `files`. */
  addMemoFile(name: string, mime: MemoFile['mime'], data: Buffer): MemoFile {
    const file: MemoFile = { id: newId(), kind: mime === 'text/markdown' ? 'markdown' : 'image', name, mime, bytes: data.length };
    this.files.set(file.id, { ...file, mime: mime as Mime, data, sent: true });
    return file;
  }

  /** The MemoFile for an id `addMemoFile` gave. */
  memoFile(id: string): MemoFile {
    const f = this.files.get(id);
    if (!f) throw new Failure('not_found', 'That file is gone.');
    return { id, kind: (f.mime as string) === 'text/markdown' ? 'markdown' : 'image', name: f.name, mime: f.mime, bytes: f.bytes };
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
      id = newId();
      up = { name: String(name).split(/[\\/]/).pop() || 'image', mime, bytes, parts: [], received: 0 };
      this.uploads.set(id, up);
    } else {
      up = this.uploads.get(id);
      if (!up) throw new Failure('not_found', 'That upload is unknown or expired.');
      if (offset !== up.received)
        throw new Failure('bad_request', 'offset is not what the daemon holds.', { field: 'offset', received: up.received });
    }
    up.parts.push(chunk);
    up.received += chunk.length;
    if (up.received < up.bytes) return { upload: id, received: up.received };
    this.uploads.delete(id);
    const all = Buffer.concat(up.parts);
    if (all.length !== up.bytes || typeOf(all) !== up.mime) throw badRequest('The file is not the type it says.', 'data');
    const attachment: Attachment = { id: newId(), name: up.name, mime: up.mime, bytes: up.bytes };
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
    return {
      mime: f.mime,
      bytes: f.data.length,
      offset,
      data: f.data.subarray(offset, end).toString('base64'),
      done: end >= f.data.length,
    };
  }

  /**
   * What a message carries (x-attachments, flow): the images its body names, then the listed ids (chat.send attachments) it does not name,
   * each once and at most 6 in all, checked as a task's text is and claimed. Not listed with a task. An id may be sent again.
   */
  message = (body: string, ids: unknown, task: unknown): { attachments: Attachment[]; parts?: (TextPart | ImagePart)[] } => {
    if (ids !== undefined && (!Array.isArray(ids) || !ids.length || ids.length > MAX_PER_MESSAGE || task !== undefined))
      throw badRequest('attachments is 1 to 6 attachment ids, and not with a task.', 'attachments');
    const named = refs(body);
    const extra = [...new Set(((ids ?? []) as unknown[]).map(String))].filter((id) => !named.includes(id));
    if (extra.length && new Set([...named, ...extra]).size > MAX_PER_MESSAGE) throw badRequest('At most 6 images to a message.', 'attachments');
    this.check(body, '', 'body', false, () => new Set());
    for (const id of extra) this.checkId(id, 'attachments');
    for (const id of extra) this.files.get(id)!.sent = true;
    const image = (id: string): Attachment => {
      const { data: _d, sent: _s, ...a } = this.files.get(id)!;
      return a;
    };
    const attachments = [...new Set(named)].filter((id) => this.files.has(id)).map(image).concat(extra.map(image));
    let parts = this.parts(body, false);
    if (extra.length) parts = [...(parts ?? (body ? [{ text: body }] : [])), ...extra.map((id) => ({ image: image(id), caption: '' }))];
    return { attachments, ...(parts ? { parts } : {}) };
  };

  /**
   * What a task write may name in `field` (x-attachments, texts): at most 6 images, each finished and kept, and for an agent only one a task
   * already shows. An image the replaced text (`was`) names is not checked again. The new ones are claimed, as a message's are.
   */
  check(text: string, was: string, field: string, agent: boolean, shown: () => Set<string>): void {
    const ids = refs(text);
    if (ids.length > MAX_PER_MESSAGE) throw invalidParams(`${field} names more than 6 images`, field);
    const kept = new Set(refs(was));
    const named = ids.filter((id) => !kept.has(id));
    for (const id of named) {
      this.checkId(id, field);
      if (agent && !shown().has(id)) throw refused('role', `attachment:${id} is named by no task: an agent names only an image a task shows`);
    }
    for (const id of named) this.files.get(id)!.sent = true;
  }

  /** Whether a write may name id in field: well formed, its upload finished, still kept. */
  private checkId(id: string, field: string): void {
    if (!/^[0-9a-f]{16}$/.test(id)) throw invalidParams(`attachment:${id} is not an attachment id`, field);
    if (this.uploads.has(id)) throw invalidParams(`attachment:${id} is an upload that has not finished`, field);
    if (!this.files.has(id)) throw new Failure('not_found', `no image ${id}`, { what: 'attachment', name: id });
  }

  /** text cut at the images it names (TextPart, ImagePart), or undefined when it names none; an agent gets each image's path. */
  parts(text: string, agent: boolean): (TextPart | ImagePart)[] | undefined {
    const out: (TextPart | ImagePart)[] = [];
    let at = 0;
    for (const m of text.matchAll(/!\[([^\]]*)\]\(attachment:([^)]*)\)/g)) {
      if (m.index > at) out.push({ text: text.slice(at, m.index) });
      at = m.index + m[0].length;
      const f = this.files.get(m[2]!);
      if (!f) out.push({ image: { id: m[2]! }, caption: m[1]!, gone: true });
      else {
        const { data: _d, sent: _s, ...image } = f;
        out.push({ image, caption: m[1]!, ...(agent ? { path: `/fake-daemon/attachments/${f.id}.${f.mime.slice(6)}` } : {}) });
      }
    }
    if (!out.length) return undefined;
    if (at < text.length) out.push({ text: text.slice(at) });
    return out;
  }

  /** The attachment.* methods. */
  call(method: string, p: Record<string, unknown>, ok: (said: string, extra?: object) => object) {
    return method === 'attachment.put' ? ok('Received.', this.put(p)) : this.get(p);
  }

  private unsent() {
    return (
      [...this.files.values()].filter((f) => !f.sent).reduce((n, f) => n + f.bytes, 0) +
      [...this.uploads.values()].reduce((n, u) => n + u.bytes, 0)
    );
  }
}

/** A write's params with image data swapped for its digest: what the replay store keeps to tell a retry from a different call. */
export const keptParams = (p: Record<string, unknown>): Record<string, unknown> =>
  typeof p.data === 'string' ? { ...p, data: createHash('sha1').update(p.data).digest('hex') } : p;
