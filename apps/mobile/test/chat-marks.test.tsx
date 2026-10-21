import { describe, expect, it, vi } from 'vitest';
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

// The marker is a Modal, drawn outside the screen's own container.
const $m = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);

describe('marking a picked picture in Chat', () => {
  it('opens the marker from the thumbnail of an arrived picture with Undo disabled, and Done with no marks sends the plain picture', async () => {
    const m = await mount();
    await openChat();
    await press($('attach-image'));
    await until(() => $(`image-mark-${chipKey()}`)?.getAttribute('aria-disabled') !== 'true' && $(`image-mark-${chipKey()}`), 'the thumbnail');
    await press($(`image-mark-${chipKey()}`));
    await until(() => $m('mark-done'), 'the marker');
    expect($m('mark-undo')?.getAttribute('aria-disabled')).toBe('true');
    await press($m('mark-done'));
    await until(() => !$m('mark-done'), 'the marker closing');
    expect($(`image-marked-${chipKey()}`)).toBeNull(); // nothing kept: not marked
    await press($('chat-send'));
    await until(() => $('pictures'), 'the picture in the sent message');
    m.store.stop();
  });

  it('Cancel leaves the chip as it was', async () => {
    const m = await mount();
    await openChat();
    await press($('attach-image'));
    await until(() => $(`image-mark-${chipKey()}`)?.getAttribute('aria-disabled') !== 'true' && $(`image-mark-${chipKey()}`), 'the thumbnail');
    await press($(`image-mark-${chipKey()}`));
    await press($m('mark-cancel'));
    await until(() => !$m('mark-cancel'), 'the marker closing');
    expect($(`image-mark-${chipKey()}`)).not.toBeNull();
    m.store.stop();
  });
});
