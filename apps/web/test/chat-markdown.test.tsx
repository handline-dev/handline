// A chat message shows its Markdown: headings, lists, bold, code and fenced code; HTML in it is text, never elements.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const BODY = [
  '## Plan',
  'Run **both** suites, then `pnpm test`.',
  '',
  '- first',
  '- second',
  '1. one',
  '2. two',
  '```',
  'a <b>x</b> line',
  '```',
  '# 12 is broken, see `T1684`, and *.ts and *.js in src/*.ts',
  '```',
  'fence T1684',
  '```',
  '<img src=x onerror=alert(1)> and <script>1</script>',
].join('\n');
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('Markdown in a chat message', () => {
  it('renders the marks and keeps HTML as text', async () => {
    const s = setup();
    s.daemon.chat.seed(ARCH, { from: ARCH, to: 'human', body: BODY });
    location.hash = `#/agents/${ARCH}`;
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
    });
    const find = () => [...box.querySelectorAll<HTMLElement>('.msg-body')].find((b) => b.textContent?.includes('pnpm test'));
    for (let i = 0; i < 300 && !find(); i++) await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
    const body = find()!;
    expect(body.querySelector('[role=heading]')?.textContent).toBe('Plan');
    expect(body.querySelector('strong')?.textContent).toBe('both');
    expect(body.querySelector('p code')?.textContent).toBe('pnpm test');
    expect([...body.querySelectorAll('ul li')].map((l) => l.textContent)).toEqual(['first', 'second']);
    expect([...body.querySelectorAll('ol li')].map((l) => l.textContent)).toEqual(['one', 'two']);
    expect(body.querySelector('pre')?.textContent).toBe('a <b>x</b> line');
    // T<N> stays a link inside inline code and inside a fence; # 12 is no heading; a lone star is no italic.
    expect(body.querySelectorAll('[role=heading]').length).toBe(1);
    expect(body.querySelector('p code [data-task="1684"]')).not.toBeNull();
    expect(body.querySelector('pre [data-task="1684"]')).not.toBeNull();
    expect(body.querySelector('em')).toBeNull();
    expect(body.textContent).toContain('*.ts and *.js in src/*.ts');
    expect(body.querySelector('img, script, pre b')).toBeNull();
    expect(body.textContent).toContain('<img src=x onerror=alert(1)>');
  });
  it('a shell fence is a command card: Copy, a reason when risky, the last run, and Run asks first', async () => {
    const s = setup();
    const id = s.daemon.chat.seed(ARCH, {
      from: ARCH,
      to: 'human',
      body: 'Try:\n```bash\ntrue\n```\nand\n```bash\nrm -rf build\n```\n```ts\nlet x\n```',
    });
    s.daemon.chat.change(s.daemon.chat.find(id), {
      runs: [{ block: `${id}:0`, terminal: '%sh9', started_at: new Date().toISOString(), state: 'done', exit: 0, seconds: 3 }],
    });
    location.hash = `#/agents/${ARCH}`;
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
    });
    const wait = async (cond: () => unknown) => {
      for (let i = 0; i < 300 && !cond(); i++) await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
    };
    await wait(() => box.querySelector('.command-card'));
    const cards = [...box.querySelectorAll<HTMLElement>('.command-card')];
    expect(cards.map((c) => c.querySelector('code')?.textContent)).toEqual(['true', 'rm -rf build']);
    expect(cards[0]!.querySelector('[data-run]')?.textContent).toBe('✓ Done · exit 0 · 3sOpen output');
    expect(cards[0]!.textContent).not.toContain('Deletes files');
    expect(cards[1]!.textContent).toContain('Deletes files for good.');
    expect([...box.querySelectorAll('pre')].filter((p) => !p.closest('.command-card')).length).toBe(1);
    const button = (c: HTMLElement, label: string) =>
      [...c.querySelectorAll<HTMLButtonElement>('button')].find((b) => (b.title || b.getAttribute('aria-label') || '').startsWith(label));
    await act(async () => button(cards[1]!, 'Run in ')!.click());
    expect(cards[1]!.querySelector('[role=alertdialog]')?.textContent).toMatch(/It runs on .* as you\./);
    await act(async () => button(cards[1]!.querySelector<HTMLElement>('[role=alertdialog]')!, 'Run')!.click());
    const runs = async () => (await s.client.call('chat.messages', { agent: ARCH, scope: 'mine' })).messages.find((m) => m.id === id)?.runs;
    for (let i = 0; i < 100 && !(await runs())?.some((r) => r.block === `${id}:1`); i++)
      await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
    expect((await runs())?.find((r) => r.block === `${id}:1`)?.state).toBe('running');
  });
});
