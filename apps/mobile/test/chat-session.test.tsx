import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from './harness.tsx';

const HANDOFF = 'Continue the phone chat: sessions, then the prompts.';

async function open(handoff: string | null) {
  const m = await mount(
    (s) => s.daemon.chat.seedItem(ARCH, { kind: 'session', session: { id: 's2', started: new Date().toISOString(), handoff } }),
    undefined,
    'founder',
  );
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('chat-input'), 'the chat');
  return m;
}
const marks = () => [...box_().querySelectorAll<HTMLElement>('[data-testid^="session-"]:not([data-testid^="session-open-"])')];
const mark = () => marks().find((m) => m.textContent?.includes('Handoff')) ?? marks()[0];
const box_ = () => document.body;

describe('the session mark in the chat', () => {
  it('shows where a session started, and a session with a handoff opens it in a sheet', async () => {
    const m = await open(HANDOFF);
    await until(mark, 'the mark');
    expect(mark()!.textContent).toContain('Session started');
    expect(mark()!.textContent).toContain('Handoff');
    expect($('handoff-text')).toBeNull();
    await press(box_().querySelector<HTMLElement>('[data-testid^="session-open-"]'));
    expect($('handoff-text')!.textContent).toBe(HANDOFF);
    expect($('sheet-submit')).toBeNull();
    expect($('sheet-cancel')!.textContent).toBe('Close');
    await press($('sheet-cancel'));
    expect($('sheet')).toBeNull();
    m.store.stop();
  });

  it('shows the mark of a session without a handoff and offers nothing to open', async () => {
    const m = await open(null);
    await until(() => mark()?.textContent?.includes('Session started'), 'the mark');
    expect(mark()!.textContent).not.toContain('Handoff');
    expect(box_().querySelector('[data-testid^="session-open-"]')).toBeNull();
    m.store.stop();
  });
});
