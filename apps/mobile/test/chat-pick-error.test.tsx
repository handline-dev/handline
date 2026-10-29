import { describe, expect, it, vi } from 'vitest';
import { $, mount, pickTab, press, text, until } from '#test/harness.tsx';

const AUD = 'handline.auditor';
let fail: unknown = new Error('Allow access to your photos in Settings');

// The photo library is native; the test makes it refuse to open.
vi.mock('#src/ImagePick.ts', () => ({ pickImages: () => Promise.reject(fail) }));

const openChat = async () => {
  await until(() => $(`agent-${AUD}`), 'the rows');
  await press($(`agent-${AUD}`));
  await pickTab('chat');
  await until(() => $('attach-image'), 'the attach button');
};

describe('the photo library does not open', () => {
  it("says the library's own reason, adds no picture and keeps send off", async () => {
    const m = await mount();
    await openChat();
    await press($('attach-image'));
    await until(() => $('image-problem'), 'the problem');
    expect(text($('image-problem'))).toBe('Allow access to your photos in Settings');
    expect($('image-chips')?.querySelector('[data-testid^="image-chip-"]') ?? null).toBeNull();
    expect($('chat-send')?.getAttribute('aria-disabled')).toBe('true');
    m.store.stop();
  });

  it('says it plainly when the refusal carries no message', async () => {
    fail = 'denied';
    const m = await mount();
    await openChat();
    await press($('attach-image'));
    await until(() => $('image-problem'), 'the problem');
    expect(text($('image-problem'))).toBe('The photo library did not open');
    m.store.stop();
  });
});
