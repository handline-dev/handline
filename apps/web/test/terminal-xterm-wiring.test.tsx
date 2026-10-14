// createXterm's wiring on the real xterm: the copy key and the guarded paste are registered on the host, in front of xterm's own handlers.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createXterm } from '../src/terminal/xterm.ts';

const created: { dispose(): void }[] = [];
afterEach(() => {
  created.splice(0).forEach((t) => t.dispose());
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

function open() {
  const host = document.body.appendChild(document.createElement('div'));
  const pane = createXterm(host);
  created.push(pane);
  const sent: string[] = [];
  pane.term.onData((d) => sent.push(d));
  const textarea = host.querySelector('textarea')!;
  return { host, pane, sent, textarea };
}
// happy-dom has no confirm of its own.
const stubConfirm = () => {
  const ask = vi.fn(() => false);
  vi.stubGlobal('confirm', ask);
  return ask;
};
const pasteEvent = (text: string) => {
  const e = new Event('paste', { bubbles: true, cancelable: true }) as Event & { clipboardData: unknown };
  e.clipboardData = { getData: () => text };
  return e;
};

describe('createXterm wiring', () => {
  it('a paste over 4 KB reaches the terminal only after the confirm', async () => {
    const ask = stubConfirm(); // before the pane: the guard takes the global confirm when it is built
    const { textarea, sent } = open();
    const big = 'x'.repeat(5 * 1024);
    const refused = pasteEvent(big);
    textarea.dispatchEvent(refused);
    expect(ask).toHaveBeenCalledOnce();
    expect(refused.defaultPrevented).toBe(true);
    expect(sent.join('')).toBe('');
    ask.mockReturnValue(true);
    textarea.dispatchEvent(pasteEvent(big));
    expect(sent.join('')).toContain(big);
  });

  it('a small paste goes through xterm without asking', () => {
    const ask = stubConfirm();
    const { textarea, sent } = open();
    textarea.dispatchEvent(pasteEvent('x'.repeat(100)));
    expect(ask).not.toHaveBeenCalled();
    expect(sent.join('')).toContain('x'.repeat(100));
  });

  it('Ctrl+C with a selection copies and sends nothing; without one it sends the interrupt', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true });
    const { pane, sent, textarea } = open();
    const term = pane.term as unknown as { select(c: number, r: number, n: number): void; write(d: string, cb: () => void): void };
    await new Promise<void>((r) => term.write('hello', r));
    const ctrlC = () =>
      textarea.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'c', code: 'KeyC', keyCode: 67, ctrlKey: true, bubbles: true, cancelable: true }),
      );
    term.select(0, 0, 5);
    ctrlC();
    expect(write).toHaveBeenCalledWith('hello');
    expect(sent).not.toContain('\x03');
    (pane.term as unknown as { clearSelection(): void }).clearSelection();
    ctrlC();
    expect(sent).toContain('\x03');
  });
});
