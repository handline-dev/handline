// U9: the prompt card for each kind answers through prompt.answer; "always" only for dialogs and only when rules can be kept; a prompt that
// changed under the click sends nothing and the card refreshes.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import type { Prompt } from '@handline/client';
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
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null) => el?.textContent ?? '';
const button = (label: string) => $$('.card.prompt button').find((b) => text(b).includes(label));

const prompt = (p: Partial<Prompt>): Prompt => ({
  id: 'p1',
  agent: ARCH,
  kind: 'dialog',
  tool: null,
  command: null,
  reason: null,
  lines: ['Overwrite the file?'],
  since: '2026-10-07T00:00:00Z',
  options: [],
  ...p,
});

async function show(p: Prompt, role: 'founder' | 'member' = 'founder') {
  const s = setup({ role });
  s.daemon.sessions.get(ARCH)!.prompt = p;
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('.card.prompt'), 'the prompt card');
  return s;
}
const received = (s: Awaited<ReturnType<typeof show>>) => s.daemon.sessions.get(ARCH)!.received.map((r) => r.value);

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

const YES_NO = [
  { key: '1', label: 'Yes', answer: 'yes' as const, highlighted: true },
  { key: '2', label: 'No', answer: 'no' as const },
];

describe('U9 prompt cards', () => {
  it('a permission offers Yes and No only, never an always option, and Yes answers with its key', async () => {
    const s = await show(
      prompt({
        kind: 'permission',
        tool: 'Bash',
        command: 'git push',
        options: [...YES_NO, { key: '3', label: "Yes, and don't ask again", answer: 'yes' }],
      }),
    );
    expect(text($('.card-title'))).toContain('Permission: Bash');
    expect($$('.card.prompt .card-actions button').map((b) => text(b))).toEqual(['Yes 1', 'No 2']);
    await click(button('Yes')!);
    await until(() => received(s).length, 'the answer');
    expect(received(s)).toEqual(['1']);
  });

  it('a dialog shows its options in order with the always one marked, when rules can be kept', async () => {
    const s = await show(
      prompt({
        options: [...YES_NO.slice(0, 1), { key: '2', label: 'Yes, always', answer: 'yes' }, { key: '3', label: 'No', answer: 'no' }],
      }),
    );
    expect($$('.card.prompt .card-actions button').map((b) => text(b))).toEqual(['Yes 1', 'Yes, always 2', 'No 3']);
    expect($$('.card.prompt .card-actions button')[1]!.querySelector('svg[data-icon="retry"]')).not.toBeNull(); // the always one carries the mark as an icon
    expect(text($('.card .note'))).toContain('saves a rule for next time');
    await click(button('always')!);
    await until(() => received(s).length, 'the answer');
    expect(received(s)).toEqual(['2']);
  });

  it('a dialog leaves the always option out when the caller cannot keep a rule', async () => {
    await show(
      prompt({ options: [YES_NO[0]!, { key: '2', label: 'Yes, always', answer: 'yes' }, { key: '3', label: 'No', answer: 'no' }] }),
      'member',
    );
    expect($$('.card.prompt .card-actions button').map((b) => text(b))).toEqual(['Yes 1', 'No 3']);
    expect($('.card .note')).toBeNull();
  });

  it('a question lists numbered options, and Type something sends the typed text', async () => {
    const s = await show(
      prompt({
        kind: 'question',
        lines: ['Which branch?'],
        options: [
          { key: '1', label: 'main', answer: 'other' },
          { key: '2', label: 'dev', answer: 'other' },
          { key: 'esc', label: 'Cancel (Esc)', answer: 'other' },
        ],
      }),
    );
    expect($$('.card.prompt ol.opts li').map((b) => text(b))).toEqual(['1 main', '2 dev']);
    await click(button('Type something')!);
    const input = $('.card input[type=text]') as HTMLInputElement;
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      set.call(input, 'release');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(button('Send')!);
    await until(() => received(s).length, 'the answer');
    expect(received(s)).toEqual(['release']);
  });

  it('a multi-select question submits the checked keys in option order', async () => {
    const s = await show(
      prompt({
        kind: 'question',
        multi: true,
        lines: ['Which targets?'],
        options: [
          ...['a', 'b', 'c'].map((l, i) => ({ key: String(i + 1), label: l, answer: 'other' as const })),
          { key: 'esc', label: 'Cancel (Esc)', answer: 'other' as const },
        ],
      }),
    );
    expect(button('Submit')!.hasAttribute('disabled')).toBe(true);
    const boxes = $$('.card input[type=checkbox]');
    await click(boxes[2]!);
    await click(boxes[0]!);
    await click(button('Submit')!);
    await until(() => received(s).length, 'the answer');
    expect(received(s)).toEqual(['1', '3']);
  });

  it('a multi-select keeps Cancel out of the checkboxes and sends only esc from its own button', async () => {
    const s = await show(
      prompt({
        kind: 'question',
        multi: true,
        options: [
          { key: '1', label: 'a', answer: 'other' },
          { key: 'esc', label: 'Cancel (Esc)', answer: 'other' },
        ],
      }),
    );
    expect($$('.card input[type=checkbox]')).toHaveLength(1);
    await click(button('Cancel')!);
    await until(() => received(s).length, 'the answer');
    expect(received(s)).toEqual(['esc']);
  });

  it('the fake refuses typed text on a prompt that is not a question', async () => {
    const s = await show(prompt({ options: YES_NO }));
    await expect(
      s.client.call('prompt.answer', { agent: ARCH, prompt: 'p1', choice: { text: 'x' }, request_id: 'r1' }),
    ).rejects.toMatchObject({
      code: 'bad_request',
    });
  });

  it('an option key pressed on the card answers it; Enter answers the highlighted option', async () => {
    const s = await show(prompt({ options: YES_NO }));
    await act(async () => {
      $('.card.prompt')!.dispatchEvent(new KeyboardEvent('keydown', { key: '2', bubbles: true }));
    });
    await until(() => received(s).length, 'the answer');
    expect(received(s)).toEqual(['2']);
  });

  it('Enter on the card answers the highlighted option', async () => {
    const s = await show(prompt({ options: YES_NO }));
    await act(async () => {
      $('.card.prompt')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    await until(() => received(s).length, 'the answer');
    expect(received(s)).toEqual(['1']);
  });

  it('a prompt that changed under the click sends nothing, says so, and shows the new prompt in place', async () => {
    const s = await show(prompt({ kind: 'permission', tool: 'Bash', command: 'git push old', options: YES_NO }));
    s.daemon.sessions.get(ARCH)!.prompt = prompt({ id: 'p2', kind: 'permission', tool: 'Bash', command: 'git push new', options: YES_NO });
    await click(button('Yes')!);
    await until(() => text($('.card.prompt')).includes('git push new'), 'the new prompt');
    expect(text($('.card.prompt'))).toContain('This prompt changed');
    expect(received(s)).toEqual([]);
  });

  it('after a refusal that showed the new prompt, answering that one does not claim it was answered elsewhere', async () => {
    const s = await show(prompt({ kind: 'permission', tool: 'Bash', command: 'git push old', options: YES_NO }));
    s.daemon.sessions.get(ARCH)!.prompt = prompt({ id: 'p2', kind: 'permission', tool: 'Bash', command: 'git push new', options: YES_NO });
    await click(button('Yes')!);
    await until(() => text($('.card.prompt')).includes('git push new'), 'the new prompt');
    await click(button('Yes')!);
    await until(() => !$('.card.prompt'), 'the card to go');
    await wait(50);
    expect(text($('.cards'))).not.toContain('Answered elsewhere');
    expect(received(s)).toEqual(['1']);
  });

  it('a prompt that is gone when answered collapses to "Answered elsewhere"', async () => {
    const s = await show(prompt({ kind: 'permission', tool: 'Bash', command: 'git push', options: YES_NO }));
    s.daemon.sessions.get(ARCH)!.prompt = null;
    await click(button('Yes')!);
    await until(() => !$('.card.prompt') && text($('.cards')).includes('Answered elsewhere'), 'the collapsed card');
    expect(received(s)).toEqual([]);
  });
});
