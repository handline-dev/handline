// Event cards in the chat (a proposal, a held start, a stall) share one look, tinted by kind, and tell an applied change from a start that waits.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const AUD = 'handline.auditor';
let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
async function until(ok: () => unknown, what: string) {
  for (let t = 0; t < 3000; t += 10) {
    if (ok()) return;
    await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
  }
  throw new Error(`timed out waiting for ${what}`);
}
async function mount(s: ReturnType<typeof setup>) {
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
  await until(() => $('.chat-foot'), 'the chat');
}
const rename = (id: number, agent: string, follow_up: { agent: string; state: string; text: string }) => ({
  id,
  kind: 'settings',
  agent,
  title: `rename ${agent}`,
  diff: `rename ${agent}`,
  state: 'approved',
  settled: new Date().toISOString(),
  follow_up,
});

describe('event cards', () => {
  it('one look for every kind, tinted: waiting amber, failed red', async () => {
    const s = setup();
    const hr = s.daemon.script.hr;
    hr.raise({ id: 910, title: 'settings handline.auditor', diff: 'effort: high', state: 'open' });
    hr.raise({ id: 911, title: 'retire handline.x', diff: 'retire', state: 'apply_failed', error: 'worktree is dirty' });
    s.daemon.sessions.get(ARCH)!.fx.view = {
      status: { state: 'waiting', reason: 'load', text: 'held: machine load', since: new Date().toISOString(), until: null, tasks: [] },
    };
    await mount(s);
    await until(() => $('.card-held') && $('[data-proposal="910"]') && $('[data-proposal="911"]'), 'the cards');
    for (const el of [$('.card-held'), $('[data-proposal="910"]'), $('[data-proposal="911"]')])
      expect(el!.classList.contains('event')).toBe(true);
    expect($('.card-held')!.dataset.tone).toBe('wait');
    expect($('[data-proposal="910"]')!.dataset.tone).toBe('wait');
    expect($('[data-proposal="911"]')!.dataset.tone).toBe('bad');
  });

  it('an applied change whose start waits for the load says Applied, never Not applied, and Start now starts it', async () => {
    const s = setup();
    s.daemon.script.hr.raise(rename(912, ARCH, { agent: ARCH, state: 'waiting', text: 'held: machine load - load 3.09 per core' }));
    await mount(s);
    await until(() => $('[data-proposal="912"]'), 'the card');
    const card = $('[data-proposal="912"]')!;
    expect(card.textContent).toContain('Applied');
    expect(card.textContent).toContain(`${ARCH} starts when the load drops`);
    expect(card.textContent).not.toContain('Not applied');
    expect(card.dataset.tone).toBe('wait');
    expect([...card.querySelectorAll('button')].some((b) => b.getAttribute('aria-label') === 'Approve')).toBe(false);
    await act(async () => ([...card.querySelectorAll('button')].find((b) => b.textContent === 'Start now') as HTMLElement).click());
    await until(() => !$('[data-follow-up]'), 'the waiting card gone once the agent started');
    expect($('[data-proposal="912"]')!.classList.contains('decided')).toBe(true); // it is now a past event, in the timeline
  });

  it('a start that failed shows the daemon text in red with the same button', async () => {
    const s = setup();
    s.daemon.script.hr.raise(rename(913, AUD, { agent: AUD, state: 'failed', text: 'start failed: no free account' }));
    await mount(s);
    await until(() => $('[data-proposal="913"]'), 'the card');
    const card = $('[data-proposal="913"]')!;
    expect(card.textContent).toContain('start failed: no free account');
    expect(card.textContent).not.toContain('Not applied');
    expect(card.dataset.tone).toBe('bad');
    expect([...card.querySelectorAll('button')].some((b) => b.textContent === 'Start now')).toBe(true);
  });
});
