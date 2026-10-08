// The send box of a structured agent: a message's receipt (queued, sent, confirmed, unknown), now or queue while a turn runs, and the
// queued message's send-now and take-back, against the fake daemon's delivery.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const AUD = 'handline.auditor'; // a structured session
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
const typeInto = (el: HTMLTextAreaElement | HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const press = (el: Element, key: string, init: KeyboardEventInit = {}) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })));
const compose = () => $('.compose') as HTMLTextAreaElement;
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));

const receipt = (body: string) => msg(body)?.querySelector('.receipt')?.getAttribute('data-receipt');
const button = (m: Element | undefined, label: string) => [...(m?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);

async function mount(s = setup()) {
  const store = new Store(s.client);
  location.hash = `#/agents/${AUD}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => $('[data-tab^="terminal:"]'), 'the watching tab');
  await click($('[data-tab^="chat"]')!);
  await until(() => compose() && !compose().disabled, 'the send box');
  return s;
}
// a turn of the agent is running, as the chat's working line says
const running = (s: ReturnType<typeof setup>) =>
  void ((s.daemon.chat as unknown as { thread(a: string): { working: unknown } }).thread(AUD).working = {
    turn_running: true,
    line: 'Working',
    since: null,
  });
const send = async (body: string) => {
  await typeInto(compose(), body);
  await press(compose(), 'Enter');
};
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('a message to a structured agent', () => {
  it('is sent, then confirmed when the CLI echoes it', async () => {
    const s = await mount();
    await send('hello');
    await until(() => receipt('hello') === 'sent', 'sent');
    expect($('.compose-now')).toBeNull(); // no turn runs: nothing to choose
    expect(button(msg('hello'), 'Take back')).toBeDefined(); // written: it can still be taken back, after a confirm
    act(() => void s.daemon.chat.tick());
    await until(() => receipt('hello') === 'confirmed', 'confirmed');
  });

  it('is queued behind a running turn, written when it ends, and can be sent now', async () => {
    const s = setup();
    s.daemon.sessions.get(AUD)!.state = 'running';
    running(s);
    await mount(s);
    await until(() => $('.compose-now'), 'the now or queue choice');
    await send('later');
    await until(() => receipt('later') === 'queued', 'queued');
    await send('sooner');
    await until(() => receipt('sooner') === 'queued', 'queued too');
    await click(button(msg('later'), 'Send now')!);
    await until(() => receipt('later') === 'sent', 'sent now');
    expect(receipt('sooner')).toBe('queued');
  });

  it('joins the running turn at once when Now is ticked', async () => {
    const s = setup();
    s.daemon.sessions.get(AUD)!.state = 'running';
    running(s);
    await mount(s);
    await until(() => $('.compose-now'), 'the choice');
    await click($('.compose-now input')!);
    await send('now please');
    await until(() => receipt('now please') === 'sent', 'sent');
    expect(($('.compose-now input') as HTMLInputElement).checked).toBe(false); // back to queue for the next one
  });

  it('is withdrawn while queued: it leaves the list and its words come back to the box', async () => {
    const s = setup();
    s.daemon.sessions.get(AUD)!.state = 'running';
    running(s);
    await mount(s);
    await until(() => $('.compose-now'), 'the choice');
    await send('changed my mind');
    await until(() => receipt('changed my mind') === 'queued', 'queued');
    await click(button(msg('changed my mind'), 'Take back')!);
    await until(() => !msg('changed my mind'), 'gone from the list');
    expect(compose().value).toBe('changed my mind');
  });

  it('says it may or may not have run when it is lost after being written, and offers no resend', async () => {
    const s = await mount();
    await send('lost');
    await until(() => receipt('lost') === 'sent', 'sent');
    act(() => void s.daemon.chat.lose(AUD));
    await until(() => receipt('lost') === 'unknown', 'unknown');
    expect(text(msg('lost'))).toContain('may or may not have run');
    expect(button(msg('lost'), 'Retry')).toBeUndefined();
    expect(msg('lost')!.querySelector('[aria-label="Retry"]')).toBeNull();
    await click(button(msg('lost'), 'Take back')!); // the daemon has it written: taking it back asks first
    expect(text(document.body)).toContain('Take it back?');
    expect(msg('lost')).toBeDefined();
  });
});
