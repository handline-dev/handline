// Seeds for the chat screen.
import { crc32, deflateSync } from 'node:zlib';
import type { FakeDaemon } from '#src/daemon.ts';
import { task } from '#src/seedkit.ts';

type Seed = (d: FakeDaemon) => void;

const ARCH = 'handline.architect';
const ME = 'human';

/** A one-colour PNG of the size given: a picture with a real height for a layout check. */
function solidPng(w: number, h: number): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const out = Buffer.concat([Buffer.alloc(4), Buffer.from(type), data, Buffer.alloc(4)]);
    out.writeUInt32BE(data.length, 0);
    out.writeUInt32BE(crc32(out.subarray(4, out.length - 4)), out.length - 4);
    return out;
  };
  const head = Buffer.alloc(13);
  head.writeUInt32BE(w, 0);
  head.writeUInt32BE(h, 4);
  head.set([8, 2, 0, 0, 0], 8);
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(w * 3, 0x9a)]);
  return Buffer.concat([
    Buffer.from('89504e470d0a1a0a', 'hex'),
    chunk('IHDR', head),
    chunk('IDAT', deflateSync(Buffer.concat(Array.from({ length: h }, () => row)))),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

export const CHAT_SEEDS: Record<string, Seed> = {
  /** Chat messages with one-tap actions: OK / No on an open task the founder owns. */
  'chat-actions': (d) => {
    task(d, 6902, { id: 6902, from: ARCH, to: ME, title: 'Open task the founder owns', state: 'doing' });
    d.chat.seed(ARCH, {
      from: ARCH,
      to: ME,
      body: 'T6902 is yours. Is it fine to go ahead?',
      tasks: [{ id: 6902, title: 'Open task the founder owns' }],
      actions: [
        { key: 'ok', label: 'OK', verb: 'task.done', task: 6902, params: { task: 6902 } },
        { key: 'no', label: 'No', verb: 'task.handoff', task: 6902, params: { task: 6902, to: ARCH }, needs: 'note' },
      ],
    });
  },

  /** A long chat with the architect: 120 messages (four pages), every seventh with a picture, for paging up through history. */
  'chat-long': (d) => {
    const png = solidPng(240, 160);
    for (let n = 1; n <= 120; n++) {
      const mine = n % 2 === 0;
      const shot = n % 7 === 0 ? d.uploads.add(`shot-${n}.png`, png) : null;
      d.chat.seed(ARCH, {
        from: mine ? ME : ARCH,
        to: mine ? ARCH : ME,
        body: `Message ${n}. ${'A line of the talk. '.repeat(1 + (n % 5))}`,
        ...(shot ? { attachments: [{ id: shot.id, name: shot.name, mime: 'image/png', bytes: png.length }] } : {}),
      });
    }
  },
};
