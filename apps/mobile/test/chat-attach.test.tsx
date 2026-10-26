import { describe, expect, it, vi } from 'vitest';
import { RpcError } from '@handline/client';
import { $, mount, press, until, pickTab } from '#test/harness.tsx';

const AUD = 'handline.auditor';
const PNG = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), (c) =>
  c.charCodeAt(0),
);

// The photo library is native; the test hands the screen what a pick returns.
vi.mock('#src/ImagePick.ts', () => ({
  pickImages: async () => [
    { blob: new Blob([PNG as BlobPart], { type: 'image/png' }), name: 'shot.png', mime: 'image/png', preview: 'file:///shot.png' },
  ],
}));

const openChat = async () => {
  await until(() => $(`agent-${AUD}`), 'the rows');
  await press($(`agent-${AUD}`));
  await pickTab('chat');
  await until(() => $('attach-image'), 'the attach button');
};
const chipKey = () =>
  $('image-chips')?.querySelector('[data-testid^="image-chip-"]')?.getAttribute('data-testid')?.replace('image-chip-', '');

describe('attaching an image in Chat', () => {
  it('uploads a picked image, waits for it, then sends it with no words', async () => {
    const m = await mount();
    await openChat();
    expect($('chat-send')?.getAttribute('aria-disabled')).toBe('true');
    await press($('attach-image'));
    await until(() => chipKey(), 'the chip');
    await until(() => $('chat-send')?.getAttribute('aria-disabled') !== 'true', 'the send button');
    await press($('chat-send'));
    await until(() => $('pictures'), 'the picture in the sent message');
    expect($('image-chips')).toBeNull();
    m.store.stop();
  });

  it('keeps a failed upload with Retry and sends nothing until it has arrived', async () => {
    const m = await mount();
    const call = m.store.client.call.bind(m.store.client);
    let fail = true;
    vi.spyOn(m.store.client, 'call').mockImplementation(((method: string, p: never) =>
      method === 'attachment.put' && fail ? Promise.reject(new RpcError('refused', 'no room')) : call(method as never, p)) as never);
    await openChat();
    await press($('attach-image'));
    await until(() => $(`image-retry-${chipKey()}`), 'Retry');
    expect($('chat-send')?.getAttribute('aria-disabled')).toBe('true');
    fail = false;
    await press($(`image-retry-${chipKey()}`));
    await until(() => $('chat-send')?.getAttribute('aria-disabled') !== 'true', 'the send button');
    expect($(`image-retry-${chipKey()}`)).toBeNull();
    m.store.stop();
  });

  it('keeps a picked image when the person goes back to the list and returns to the chat, until it is sent', async () => {
    const m = await mount();
    await openChat();
    await press($('attach-image'));
    await until(() => chipKey(), 'the chip');
    await until(() => $('chat-send')?.getAttribute('aria-disabled') !== 'true', 'uploaded');
    const key = chipKey();
    await press($('back'));
    await until(() => $(`agent-${AUD}`) && !$('attach-image'), 'the list');
    await openChat();
    expect(chipKey()).toBe(key);
    expect($('chat-send')?.getAttribute('aria-disabled')).not.toBe('true');
    await press($('chat-send'));
    await until(() => $('pictures'), 'the picture in the sent message');
    await press($('back'));
    await until(() => $(`agent-${AUD}`) && !$('attach-image'), 'the list again');
    await openChat();
    expect(chipKey()).toBeUndefined();
    m.store.stop();
  });

  it('a removed chip leaves nothing to send', async () => {
    const m = await mount();
    await openChat();
    await press($('attach-image'));
    await until(() => chipKey(), 'the chip');
    await press($(`image-remove-${chipKey()}`));
    expect(chipKey()).toBeUndefined();
    expect($('chat-send')?.getAttribute('aria-disabled')).toBe('true');
    m.store.stop();
  });
});
