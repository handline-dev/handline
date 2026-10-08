// The transcript view of a structured agent, in place of its terminal, against the fake daemon's transcript stream.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import type { TranscriptEvent } from '@handline/client';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const AUD = 'handline.auditor'; // a structured session, seeded with one event of every kind
const TERM = 'handline.term-build'; // a terminal agent
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';

async function mount(agent: string, s = setup()) {
  const store = new Store(s.client);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => $('[data-tab^="terminal:"]'), 'the watching tab');
  await click($('[data-tab^="terminal:"]')!);
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the transcript of a structured agent', () => {
  it('draws every kind of event, tool calls and results folded', async () => {
    await mount(AUD);
    await until(() => $('.tr-turn'), 'the transcript');
    expect($('.xterm, .term')).toBeNull();
    expect(text($('.tr-said'))).toContain('Run the contract tests.');
    expect(text($('.tr-text'))).toBe('Running them now.');
    const folds = $$('.tr-fold');
    expect(text(folds[0])).toContain('Bash');
    expect(text(folds[1])).toContain('returned: 26 passed, 0 failed');
    expect(folds.every((f) => !(f as HTMLDetailsElement).open)).toBe(true);
    expect(text($('.tr-note[data-answer="allow"]'))).toBe('Allowed by person');
    expect($('.prompt-card, [data-prompt]')).toBeNull(); // answered: no card
    expect(text($('.tr-error'))).toContain('rate_limited');
    expect(text($('.tr-end'))).toBe('done · $0.04 · 8.2 s');
  });

  it('shows a permission card that answers through prompt.answer, then the outcome', async () => {
    const s = await mount(AUD);
    await until(() => $('.tr-turn'), 'the transcript');
    act(() => void s.daemon.transcript.ask(AUD, 't-2', 'Bash', 'ls'));
    await until(() => $$('.tr-tool').at(-1)?.querySelector('button'), 'the permission card');
    const card = $$('.tr-tool').at(-1)!;
    const allow = [...card.querySelectorAll('button')].find((b) => /allow|yes/i.test(text(b)))!;
    console.log(text(card), [...card.querySelectorAll('button')].map(text));
    await click(allow);
    await until(() => $$('.tr-end').length === 2, 'the outcome');
    expect($$('.tr-tool').at(-1)!.querySelector('button')).toBeNull();
    expect(text($$('.tr-end').at(-1))).toContain('$0.01');
    expect(text($$('.tr-note[data-answer="allow"]').at(-1))).toBe('Allowed by person');
  });

  it('follows new events at the end, and offers a jump back when scrolled up', async () => {
    const s = await mount(AUD);
    await until(() => $('.tr-turn'), 'the transcript');
    const sc = $('.tr-scroll')!;
    Object.defineProperties(sc, { scrollHeight: { value: 1000, configurable: true }, clientHeight: { value: 200, configurable: true } });
    sc.scrollTop = 0;
    await act(async () => void sc.dispatchEvent(new Event('scroll')));
    act(() => void s.daemon.transcript.push(AUD, { kind: 'text', turn: 't-3', text: 'More.' }));
    await until(() => $('.tr-newest'), 'the jump button');
    await click($('.tr-newest')!);
    expect(sc.scrollTop).toBe(1000);
    expect($('.tr-newest')).toBeNull();
  });

  it('shows a skeleton while it loads and, when the read fails, an error with a Retry that reads again', async () => {
    const s = setup();
    s.daemon.knobs.latency(300);
    s.daemon.knobs.refuse('transcript.events', 'busy', 'try later', undefined, 1);
    await mount(AUD, s);
    await until(() => $('.tr .skeleton'), 'the skeleton');
    expect($('.term-bar')).toBeNull(); // no terminal is attached while it is not known to be one
    await until(() => text($('.tr')).includes('could not be read'), 'the error');
    s.daemon.knobs.latency(0);
    await click([...$$('.tr button')].find((b) => text(b) === 'Retry')!);
    await until(() => $('.tr-turn'), 'the transcript after Retry');
  });

  it('marks where the daemon no longer keeps events after a reconnect', async () => {
    const s = await mount(AUD);
    await until(() => $('.tr-turn'), 'the transcript');
    // events the client never hears of: added to the log without the stream, then cut by retention
    const log = s.daemon.transcript.events(AUD) as TranscriptEvent[];
    const last = log.at(-1)!;
    for (let i = 1; i <= 6; i++) log.push({ ...last, kind: 'text', text: `late ${i - 1}`, seq: last.seq + i });
    s.daemon.transcript.forget(AUD, last.seq + 2);
    s.daemon.dropConnections();
    await until(() => text($('.tr')).includes('late 5'), 'the events after the gap', 600);
    expect(text($('.tr'))).toContain('Earlier events are no longer kept');
  });

  it('leaves a terminal agent its terminal', async () => {
    await mount(TERM);
    await wait(50);
    expect($('.tr')).toBeNull();
  });
});
