// A gate task's page: what it watches in the daemon's words, linked where the vendor has a page, with the result once it has one.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

async function mountGate(watch: Record<string, unknown>, state = 'doing') {
  const s = setup();
  s.daemon.tasks.seed({ id: 1750, from: 'handline', to: 'gate', title: 'Wait for PR CI', state, watch } as never);
  location.hash = '#/tasks/1750';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
  await until(() => box.querySelector('.twatch'), 'the Watches line');
  return box.querySelector<HTMLElement>('.twatch')!;
}

describe('a gate task page', () => {
  it('colours a done gate by its result: passed green, failed red, timed out grey', async () => {
    const tone = { passed: 'data-[state=done]:text-ok', failed: 'data-[state=done]:text-bad', timed_out: 'data-[state=done]:text-ink-3' };
    for (const [result, cls] of Object.entries(tone)) {
      await mountGate({ vendor: 'github', target: 'acme/app#426', until: 'ci', label: 'PR #426 CI', result }, 'done');
      const chip = box.querySelector('.tchip[data-state=done]')!;
      expect(chip.classList.contains(cls), result).toBe(true);
      expect(chip.getAttribute('title'), result).toBe(result === 'passed' ? 'done' : result === 'failed' ? 'failed' : 'timed out');
      await act(async () => root?.unmount());
      root = null;
      box.remove();
    }
  });

  it('links the label to the pull request and shows the result', async () => {
    const line = await mountGate({ vendor: 'github', target: 'acme/app#426', until: 'ci', label: 'PR #426 CI', result: 'failed' });
    expect(line.textContent).toBe('Watches PR #426 CI · failed');
    expect(line.querySelector('a')!.getAttribute('href')).toBe('https://github.com/acme/app/pull/426');
  });

  it('links an issue watch to the issue, and shows a vendor with no page as plain words', async () => {
    const issue = await mountGate({ vendor: 'github', target: 'acme/app#7', until: 'done', label: 'Issue #7 done', result: null });
    expect(issue.querySelector('a')!.getAttribute('href')).toBe('https://github.com/acme/app/issues/7');
    await act(async () => root?.unmount());
    root = null;
    box.remove();
    const plain = await mountGate({ vendor: 'jira', target: 'X-1', until: 'done', label: 'X-1 done', result: 'timed_out' });
    expect(plain.querySelector('a')).toBeNull();
    expect(plain.textContent).toBe('Watches X-1 done · timed out');
  });
});
