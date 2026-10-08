import { act } from 'react';
import { describe, expect, it } from 'vitest';
import type { Prompt } from '@handline/client';
import { ARCH, $, mount, press, text, until } from './harness.tsx';

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
const YES = { key: '1', label: 'Yes', answer: 'yes' as const, highlighted: true };
const NO = { key: '3', label: 'No', answer: 'no' as const };
const ALWAYS = { key: '2', label: 'Yes, always', answer: 'yes' as const };
const LETTERS = ['a', 'b', 'c'].map((l, i) => ({ key: String(i + 1), label: l, answer: 'other' as const }));
const ESC = { key: 'esc', label: 'Cancel (Esc)', answer: 'other' as const };

async function show(p: Prompt, role: 'founder' | 'member' = 'founder') {
  const m = await mount((s) => void (s.daemon.sessions.get(ARCH)!.prompt = p), undefined, undefined, { role });
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('prompt-card'), 'the card');
  return { ...m, received: () => m.daemon.sessions.get(ARCH)!.received.map((r) => r.value) };
}
const labels = () => [...$('prompt-card')!.querySelectorAll<HTMLElement>('[data-testid^="answer-"]')].map((b) => b.textContent);

describe('the prompt card', () => {
  it('offers a permission Yes and No only, never an always option', async () => {
    const m = await show(
      prompt({
        kind: 'permission',
        tool: 'Bash',
        command: 'git push',
        options: [YES, { ...NO, key: '2' }, { key: '3', label: "Yes, and don't ask again", answer: 'yes' }],
      }),
    );
    expect(text()).toContain('Permission: Bash');
    expect(labels()).toEqual(['Yes', 'No']);
    await press($('answer-1'));
    await until(() => m.received().length, 'the answer');
    expect(m.received()).toEqual(['1']);
    m.store.stop();
  });

  it('marks the always option of a dialog when rules can be kept, and answers with its key', async () => {
    const m = await show(prompt({ options: [YES, ALWAYS, NO] }));
    expect(labels()).toEqual(['Yes', '↻ Yes, always', 'No']);
    expect(text()).toContain('saves a rule for next time');
    await press($('answer-2'));
    await until(() => m.received().length, 'the answer');
    expect(m.received()).toEqual(['2']);
    m.store.stop();
  });

  it('leaves the always option out when the caller cannot keep a rule', async () => {
    const m = await show(prompt({ options: [YES, ALWAYS, NO] }), 'member');
    expect(labels()).toEqual(['Yes', 'No']);
    expect(text()).not.toContain('saves a rule');
    m.store.stop();
  });

  it('submits the checked keys of a multi-select in option order, with Cancel kept out of the list', async () => {
    const m = await show(prompt({ kind: 'question', multi: true, lines: ['Which targets?'], options: [...LETTERS, ESC] }));
    expect($('answer-submit')!.getAttribute('aria-disabled')).toBe('true');
    await press($('answer-3'));
    await press($('answer-1'));
    expect($('answer-1')!.textContent).toContain('☑');
    await press($('answer-submit'));
    await until(() => m.received().length, 'the answer');
    expect(m.received()).toEqual(['1', '3']);
    m.store.stop();
  });

  it('sends only esc from the Cancel button of a multi-select', async () => {
    const m = await show(prompt({ kind: 'question', multi: true, options: [LETTERS[0]!, ESC] }));
    expect(labels()).toEqual(['☐ a', 'Submit', 'Cancel']);
    await press($('answer-esc'));
    await until(() => m.received().length, 'the answer');
    expect(m.received()).toEqual(['esc']);
    m.store.stop();
  });

  it('sends the words typed to a question', async () => {
    const m = await show(prompt({ kind: 'question', lines: ['Which branch?'], options: [LETTERS[0]!, ESC] }));
    await press($('answer-type'));
    await act(async () => {
      const el = $('answer-text') as HTMLInputElement;
      Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, 'release');
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await press($('answer-text-send'));
    await until(() => m.received().length, 'the answer');
    expect(m.received()).toEqual(['release']);
    m.store.stop();
  });

  it('sends nothing and says "Answered elsewhere" when the prompt has gone', async () => {
    const m = await show(prompt({ options: [YES, NO] }));
    m.daemon.sessions.get(ARCH)!.prompt = null; // answered at another device
    await press($('answer-1'));
    await until(() => $('prompt-gone'), 'Answered elsewhere');
    expect(m.received()).toEqual([]);
    m.store.stop();
  });

  it('keeps the card and says "This prompt changed" when another prompt has replaced it', async () => {
    const m = await show(prompt({ options: [YES, NO] }));
    m.daemon.sessions.get(ARCH)!.prompt = prompt({ id: 'p2', lines: ['Delete the branch?'], options: [YES, NO] });
    await press($('answer-1'));
    await until(() => $('prompt-changed'), 'the notice');
    expect($('prompt-card')).not.toBeNull();
    expect($('prompt-gone')).toBeNull();
    expect(m.received()).toEqual([]);
    m.store.stop();
  });
});
