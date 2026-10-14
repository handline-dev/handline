// Copy and paste in the web terminal: Ctrl/Cmd+C copies a selection and sends nothing, else it is the interrupt; a paste over 4 KB asks first.
import { describe, expect, it, vi } from 'vitest';
import { PASTE_ASK_BYTES, copyKey, pasteGuard } from '../src/terminal/xterm.ts';

const key = (k: string, mods: Partial<KeyboardEvent> = {}) => ({
  type: 'keydown',
  key: k,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  ...mods,
});
const selection = (text: string | null) => ({ hasSelection: () => text !== null, getSelection: () => text ?? '' });
const pasteOf = (text: string) => ({ clipboardData: { getData: () => text }, preventDefault: vi.fn(), stopPropagation: vi.fn() });

describe('copy', () => {
  it('Ctrl+C and Cmd+C with a selection copy it and are not sent', () => {
    const write = vi.fn();
    const handle = copyKey(selection('hello'), write);
    expect(handle(key('c', { ctrlKey: true }))).toBe(false);
    expect(handle(key('c', { metaKey: true }))).toBe(false);
    expect(write).toHaveBeenCalledTimes(2);
    expect(write).toHaveBeenCalledWith('hello');
  });

  it('Ctrl+C without a selection is left to the terminal: it sends the interrupt', () => {
    const write = vi.fn();
    expect(copyKey(selection(null), write)(key('c', { ctrlKey: true }))).toBe(true);
    expect(write).not.toHaveBeenCalled();
  });

  it('other keys and Ctrl+Shift+C are left alone even with a selection', () => {
    const write = vi.fn();
    const handle = copyKey(selection('x'), write);
    expect(handle(key('v', { ctrlKey: true }))).toBe(true);
    expect(handle(key('c'))).toBe(true);
    expect(handle(key('c', { ctrlKey: true, shiftKey: true }))).toBe(true);
    expect(write).not.toHaveBeenCalled();
  });
});

describe('paste', () => {
  it('a small paste is left to xterm and asks nothing', () => {
    const paste = vi.fn();
    const ask = vi.fn(() => true);
    const e = pasteOf('x'.repeat(100));
    pasteGuard(paste, ask)(e);
    expect(ask).not.toHaveBeenCalled();
    expect(e.preventDefault).not.toHaveBeenCalled();
    expect(paste).not.toHaveBeenCalled();
  });

  it('a paste over 4 KB is held and goes through xterm only after Confirm', () => {
    const paste = vi.fn();
    const text = 'x'.repeat(5 * 1024);
    const no = pasteOf(text);
    pasteGuard(paste, () => false)(no);
    expect(no.preventDefault).toHaveBeenCalled();
    expect(paste).not.toHaveBeenCalled();
    pasteGuard(paste, () => true)(pasteOf(text));
    expect(paste).toHaveBeenCalledWith(text);
  });

  it('counts bytes, not characters', () => {
    const ask = vi.fn(() => false);
    pasteGuard(vi.fn(), ask)(pasteOf('é'.repeat(PASTE_ASK_BYTES / 2 + 1))); // 2 bytes each
    expect(ask).toHaveBeenCalled();
  });
});
