// Images on a message: the upload in chunks (attachment.put) and the read back (attachment.get). The functions take the client's `call`
// so they run in a test with no React; the hook draws the result as an object URL kept per image.
import { useEffect, useState } from 'react';
import { RpcError, type Attachment } from '@handline/client';
import { useStore } from '@handline/app-react';
import { newOperationId } from './send-ops.ts';
import { useWrite } from './write.ts';

// These mirror the daemon's limits (contract x-attachments); the contract carries them as prose, not as constants.
export const CHUNK = 512 * 1024;
export const MAX_IMAGE = 10 * 1024 * 1024;
export const MAX_PER_MESSAGE = 6;
export const IMAGE_TYPES: Attachment['mime'][] = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

const known = new Map<string, Attachment>();
/** An uploaded image remembered by id, so the row a send shows at once can draw it before the daemon's own arrives. */
export const rememberAttachment = (a: Attachment) => void known.set(a.id, a);
export const forgetAttachment = (id: string) => void known.delete(id);
/** Taken once, by the row a send shows: the daemon's own row carries the images from then on. */
export const knownAttachments = (ids: string[] = []): Attachment[] =>
  ids.flatMap((id) => {
    const a = known.get(id);
    known.delete(id);
    return a ?? [];
  });

type Call = (method: string, params: Record<string, unknown>) => Promise<unknown>;
type Mime = Attachment['mime'];

/** Why a file cannot be attached, before anything is sent; null when it can. */
export function whyNotImage(f: { type: string; size: number; name: string }): string | null {
  if (!IMAGE_TYPES.includes(f.type as Mime)) return `${f.name}: only PNG, JPEG, GIF and WebP images`;
  if (f.size > MAX_IMAGE) return `${f.name}: an image is at most 10 MB`;
  if (f.size === 0) return `${f.name}: the file is empty`;
  return null;
}

const base64 = (bytes: Uint8Array) => {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
};
const unbase64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

/**
 * Sends the file chunk by chunk. Every chunk has its own operation id, and a chunk that did not get an answer is sent again under the same one,
 * so a lost reply is never written twice; a daemon holding a different length (`offset` refused) is followed from what it says it holds.
 */
export async function uploadImage(
  call: Call,
  file: Blob,
  name: string,
  mime: Mime,
  onProgress: (sent: number, total: number) => void = () => {},
  signal?: { aborted: boolean },
): Promise<Attachment> {
  const bytes = file.size;
  let offset = 0;
  let upload: string | undefined;
  for (;;) {
    if (signal?.aborted) throw new Error('cancelled');
    const data = base64(new Uint8Array(await file.slice(offset, offset + CHUNK).arrayBuffer()));
    const params = { name, mime, bytes, data, ...(offset ? { offset } : {}), ...(upload ? { upload } : {}), request_id: newOperationId() };
    let r: { upload: string; received: number; attachment?: Attachment } | undefined;
    for (let tries = 0; !r; tries++) {
      if (signal?.aborted) throw new Error('cancelled');
      try {
        r = (await call('attachment.put', params)) as typeof r;
      } catch (e) {
        if (e instanceof RpcError && e.code === 'bad_request' && e.data?.field === 'offset' && typeof e.data.received === 'number') {
          offset = e.data.received;
          break;
        }
        // `transport` is the client's only lost-connection code (the contract has no timeout code).
        if (!(e instanceof RpcError && e.code === 'transport') || tries >= 2) throw e;
      }
    }
    if (!r) continue;
    upload = r.upload;
    offset = r.received;
    onProgress(offset, bytes);
    if (r.attachment) return r.attachment;
  }
}

/** `uploadImage` through the write door: the chunks are `attachment.put` writes. */
export function useUploadImage() {
  const put = useWrite('attachment.put');
  return (file: Blob, name: string, mime: Mime, onProgress?: (sent: number, total: number) => void, signal?: { aborted: boolean }) =>
    uploadImage((_m, p) => put.mutateAsync(p as never), file, name, mime, onProgress, signal);
}

/** The bytes of an image: its small copy in one reply, or the original read to its end. */
export async function readImage(call: Call, id: string, thumb: boolean): Promise<{ mime: Mime; bytes: Uint8Array }> {
  const parts: Uint8Array[] = [];
  let offset = 0;
  for (;;) {
    const r = (await call('attachment.get', { id, ...(thumb ? { thumb: true } : { offset }) })) as { mime: Mime; offset: number; data: string; done: boolean };
    const chunk = unbase64(r.data);
    parts.push(chunk);
    offset += chunk.length;
    if (r.done || thumb || !chunk.length) {
      const all = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
      parts.reduce((at, p) => (all.set(p, at), at + p.length), 0);
      return { mime: r.mime, bytes: all };
    }
  }
}

const urls = new Map<string, Promise<string | null>>();
/** An object URL for the image, read once per image and size; null when the daemon no longer has it. */
const urlOf = (call: Call, id: string, thumb: boolean): Promise<string | null> => {
  const key = `${id}:${thumb ? 't' : 'o'}`;
  let p = urls.get(key);
  if (!p) {
    p = readImage(call, id, thumb).then(
      ({ mime, bytes }) => URL.createObjectURL(new Blob([bytes as BlobPart], { type: mime })),
      (e) => {
        urls.delete(key); // a lost connection is tried again next time; a gone image stays null
        if (e instanceof RpcError && e.code === 'not_found') return null;
        throw e;
      },
    );
    urls.set(key, p);
  }
  return p;
};

/** Lets go of an image's object URL; the next use reads it again. */
const release = (id: string, thumb: boolean) => {
  const key = `${id}:${thumb ? 't' : 'o'}`;
  void urls.get(key)?.then((u) => u && URL.revokeObjectURL(u));
  urls.delete(key);
};

/** The picture of an attachment as a URL once read: `gone` when the daemon has dropped it (the name is shown without the picture). */
export function useAttachmentUrl(id: string, thumb: boolean): { url: string | null; gone: boolean } {
  const store = useStore();
  const [state, setState] = useState<{ url: string | null; gone: boolean }>({ url: null, gone: false });
  useEffect(() => {
    let live = true;
    urlOf((m, p) => store.client.call(m as never, p as never), id, thumb).then(
      (url) => live && setState({ url, gone: url === null }),
      () => {},
    );
    return () => {
      live = false;
      if (!thumb) release(id, false); // the original is large: kept only while it is open
    };
  }, [store, id, thumb]);
  return state;
}
