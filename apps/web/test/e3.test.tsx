// E3 from the page: a path in a message opens through the shell on a click, "In editor" round-trips the box, and a plain browser has neither.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>([['terminal', 'text']]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const text = (el: Element | null) => el?.textContent ?? '';
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const type = (v: string) =>
  act(async () => {
    const t = $('textarea.compose') as HTMLTextAreaElement;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(t, v);
    t.dispatchEvent(new Event('input', { bubbles: true }));
  });
const buttonNamed = (label: string, within = box) => [...within.querySelectorAll('button')].find((b) => text(b).trim() === label);
const inCompose = (label: string) => buttonNamed(label, $('.compose-row')!);

async function mount() {
  const s = setup();
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('textarea.compose'), 'the message box');
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  vi.unstubAllGlobals();
});

const stub = (over: object = {}) => {
  const calls = { open: [] as unknown[], compose: [] as unknown[], cancel: 0 };
  vi.stubGlobal('handline', {
    api: 1,
    screen: async () => ({ ok: true }),
    unsent: async () => ({ ok: true }),
    onNavigate: () => () => undefined,
    openPath: async (a: unknown) => (calls.open.push(a), { ok: true, how: 'opened' }),
    compose: async (a: unknown) => (calls.compose.push(a), { ok: true, text: 'edited text\n\n', changed: true }),
    cancelCompose: async () => (calls.cancel++, { ok: true }),
    ...over,
  });
  return calls;
};

async function sendMessage(body: string) {
  await type(body);
  await click(buttonNamed('Send ↵')!);
  await until(() => $$('.msg-body').some((b) => text(b).includes(body.split(' ')[0]!)), 'the message');
}

describe('E3 in the page', () => {
  it('a path in a message is a button; nothing is called until the click, then the shell gets the path without its line number and the agent', async () => {
    const calls = stub();
    await mount();
    await sendMessage('see src/a.ts:42 and ~/notes/b.md');
    const links = $$('button.path');
    expect(links.map(text)).toEqual(['src/a.ts:42', '~/notes/b.md']);
    expect(calls.open).toEqual([]); // rendering calls nothing
    await click(links[0]!);
    expect(calls.open).toEqual([{ path: 'src/a.ts', agent: ARCH }]);
  });

  it('a path the shell only revealed or could not find says so beside the text', async () => {
    stub({ openPath: async () => ({ ok: true, how: 'revealed' }) });
    await mount();
    await sendMessage('run tools/x.sh now');
    await click($('button.path')!);
    expect(text($('.path-note'))).toContain('not opened');
  });

  it('a path the shell cannot find says so beside the text', async () => {
    stub({ openPath: async () => ({ ok: false, code: 'not_found', message: 'tools/x.sh does not exist' }) });
    await mount();
    await sendMessage('run tools/x.sh now');
    await click($('button.path')!);
    expect(text($('.path-note'))).toContain('does not exist');
  });

  it('In editor sends the box text, puts the edited text back without its trailing newlines, and shows nothing sent', async () => {
    const calls = stub();
    await mount();
    await type('first draft');
    await click(inCompose('In editor')!);
    await until(() => ($('textarea.compose') as HTMLTextAreaElement).value === 'edited text', 'the edited text');
    expect(calls.compose).toEqual([{ text: 'first draft' }]);
    expect($$('.msg-body').some((b) => text(b).includes('edited text'))).toBe(false); // put in the box, not sent
  });

  it('a refusal keeps the draft and says why', async () => {
    stub({ compose: async () => ({ ok: false, code: 'terminal_editor', message: 'vim needs a terminal' }) });
    await mount();
    await type('my draft');
    await click(inCompose('In editor')!);
    await until(() => text(box).includes('vim needs a terminal'), 'the reason');
    expect(($('textarea.compose') as HTMLTextAreaElement).value).toBe('my draft');
  });

  it('text the editor left unchanged says so and changes nothing', async () => {
    stub({ compose: async () => ({ ok: true, text: 'my draft\n', changed: false }) });
    await mount();
    await type('my draft');
    await click(inCompose('In editor')!);
    await until(() => text(box).includes('Nothing changed'), 'unchanged');
    expect(($('textarea.compose') as HTMLTextAreaElement).value).toBe('my draft');
  });

  it('while the editor is open the box shows Waiting with a Cancel that calls cancelCompose; a cancelled compose leaves the draft', async () => {
    let release: (v: unknown) => void = () => undefined;
    const calls = stub({ compose: () => new Promise((r) => (release = r)) });
    await mount();
    await type('my draft');
    await click(inCompose('In editor')!);
    expect(text($('.compose-row'))).toContain('Waiting for the editor');
    await click(inCompose('Cancel')!);
    expect(calls.cancel).toBe(1);
    await act(async () => release({ ok: false, code: 'cancelled', message: 'Cancelled.' }));
    expect(($('textarea.compose') as HTMLTextAreaElement).value).toBe('my draft');
    expect(inCompose('In editor')).toBeTruthy();
  });

  it('a plain browser has no In editor button, and a path stays plain text', async () => {
    await mount();
    await sendMessage('see src/a.ts please');
    expect(buttonNamed('In editor')).toBeUndefined();
    expect($('button.path')).toBeNull();
  });

  it('a shell without these calls shows neither', async () => {
    vi.stubGlobal('handline', {
      api: 1,
      screen: async () => ({ ok: true }),
      unsent: async () => ({ ok: true }),
      onNavigate: () => () => undefined,
    });
    await mount();
    await sendMessage('see src/a.ts please');
    expect(buttonNamed('In editor')).toBeUndefined();
    expect($('button.path')).toBeNull();
  });
});
