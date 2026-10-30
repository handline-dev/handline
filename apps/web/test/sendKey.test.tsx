import { describe, expect, it, vi } from 'vitest';
import type { KeyboardEvent } from 'react';
import { sendKey } from '#src/ui/sendKey.ts';

const key = (k: string, extra: { shiftKey?: boolean; composing?: boolean } = {}) =>
  ({
    key: k,
    shiftKey: !!extra.shiftKey,
    nativeEvent: { isComposing: !!extra.composing },
    preventDefault: vi.fn(),
  }) as unknown as KeyboardEvent;

describe('the send key', () => {
  it('Enter sends and holds the newline back', () => {
    const send = vi.fn();
    const e = key('Enter');
    expect(sendKey(e, send)).toBe(true);
    expect(send).toHaveBeenCalledOnce();
    expect(e.preventDefault).toHaveBeenCalled();
  });
  it('Shift+Enter, another key and an IME confirm do not send', () => {
    const send = vi.fn();
    for (const e of [key('Enter', { shiftKey: true }), key('a'), key('Enter', { composing: true })]) expect(sendKey(e, send)).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });
});
