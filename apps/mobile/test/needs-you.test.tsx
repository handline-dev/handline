import { afterEach, describe, expect, it } from 'vitest';
import type { Prompt } from '@handline/client';
import { ARCH, $, mount, press, text, until } from './harness.tsx';

const needs = (text: string | null) => ({
  kind: 'permission' as const,
  since: new Date(Date.now() - 4 * 60_000).toISOString(),
  text,
  source: 'hook' as const,
});
const prompt: Prompt = {
  id: 'p1',
  agent: ARCH,
  kind: 'permission',
  tool: 'Bash',
  command: 'git push origin main',
  reason: null,
  lines: [],
  since: '2026-10-07T00:00:00Z',
  options: [
    { key: '1', label: 'Yes', answer: 'yes', highlighted: true },
    { key: '2', label: 'No', answer: 'no' },
  ],
};

// The agent fixtures outlive a test, so the view a test sets is taken off again.
const set = new Set<{ view?: unknown }>();
afterEach(() => {
  for (const fx of set) fx.view = undefined;
  set.clear();
});

describe('needs you', () => {
  it('has no card while needs_you is not set', async () => {
    const n = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-input'), 'the chat');
    expect($('needs-you')).toBeNull();
    n.store.stop();
  }, 20000);

  it('shows the reason on the list row, puts the agent first, and opens a card in its chat that answers the prompt', async () => {
    const m = await mount((s) => {
      const a = s.daemon.sessions.get(ARCH)!;
      set.add(a.fx);
      a.fx.view = { needs_you: needs('Bash: git push origin main') };
      a.prompt = prompt;
    });
    await until(() => $(`needs-${ARCH}`), 'the reason line');
    expect($(`needs-${ARCH}`)!.textContent).toContain('Bash: git push origin main');
    expect(document.querySelector('[data-testid^="agent-"]')!.getAttribute('data-testid')).toBe(`agent-${ARCH}`);
    await press($(`agent-${ARCH}`));
    await until(() => $('needs-you'), 'the card');
    expect($('needs-you')!.textContent).toContain('Permission');
    await until(() => $('prompt-card'), 'the answer card inside');
    expect($('needs-you')!.contains($('prompt-card'))).toBe(true);
    m.store.stop();
  }, 20000);

  it('says to answer at the computer when no prompt is served, and has no card without needs_you', async () => {
    const m = await mount((s) => {
      const fx = s.daemon.sessions.get(ARCH)!.fx;
      set.add(fx);
      fx.view = { needs_you: needs(null) };
    });
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('needs-you'), 'the card');
    expect(text()).toContain('Answer at the computer.');
    m.store.stop();
  }, 20000);
});
