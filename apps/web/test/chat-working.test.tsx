// The working indicator follows the agent being busy, whoever wrote the message it is busy with.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
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

describe('the working indicator', () => {
  it('shows while the agent is busy, also on a message from another agent', async () => {
    const s = setup();
    location.hash = `#/agents/${ARCH}`;
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
    });
    await until(() => box.querySelector('textarea.compose'), 'the chat');
    expect(box.querySelector('.working')).toBeNull();
    s.daemon.chat.seed(ARCH, { from: 'handline.auditor', to: ARCH, body: 'rebase the pane branch' });
    s.daemon.chat.setWorking(ARCH, true, 'Finding large disk users');
    await until(() => box.querySelector('.working')?.getAttribute('title')?.includes('Finding large disk users · '), 'the indicator');
    expect(box.textContent).toContain('Stop');
    s.daemon.chat.setWorking(ARCH, false);
    await until(() => box.querySelector('.working') === null, 'the indicator gone');
  });

  it("counts this turn's commands, shows the newest, opens them on a click and folds to a count after the turn", async () => {
    const s = setup();
    s.daemon.chat.setWorking(ARCH, true);
    s.daemon.chat.seedItem(ARCH, { kind: 'tool', tool: 'Bash', text: 'git status' });
    s.daemon.chat.seedItem(ARCH, { kind: 'tool', tool: 'Read', text: 'a.ts' });
    location.hash = `#/agents/${ARCH}`;
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
    });
    await until(() => box.querySelector('textarea.compose'), 'the chat');
    await until(() => box.querySelector('.working')?.textContent?.includes('Reading a.ts · '), 'the live line');
    expect(box.querySelector('[data-testid="turn-steps"]')).toBeNull();
    await act(async () => box.querySelector<HTMLButtonElement>('.working')!.click());
    expect(box.querySelectorAll('[data-testid="turn-steps"] [data-kind="tool"]').length).toBe(2);
    s.daemon.chat.setWorking(ARCH, false);
    await until(() => box.querySelector('.ran')?.textContent?.startsWith('Ran 2 commands'), 'the folded line');
  });

  async function mountWithTurn(s: ReturnType<typeof setup>) {
    s.daemon.chat.setWorking(ARCH, true);
    s.daemon.chat.seedItem(ARCH, { kind: 'tool', tool: 'Read', text: 'a.ts' });
    location.hash = `#/agents/${ARCH}`;
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
    });
    await until(() => box.querySelector('.working'), 'the live line');
  }

  it('Stop interrupts the turn: the daemon is asked and the line goes', async () => {
    const s = setup();
    await mountWithTurn(s);
    const stop = [...box.querySelectorAll<HTMLButtonElement>('.working-slot button')].find((b) => b.textContent === 'Stop')!;
    await act(async () => stop.click());
    const got = () => s.daemon.sessions.get(ARCH)!.received.some((r) => r.kind === 'key' && r.value === 'esc');
    await until(got, 'session.interrupt reaching the daemon');
  });

  it('names the task after the turn when the agent holds one', async () => {
    const s = setup();
    const held = s.daemon.sessions.get(ARCH)!.fx.task!;
    await mountWithTurn(s);
    s.daemon.chat.setWorking(ARCH, false);
    await until(() => box.querySelector('.ran')?.textContent?.includes(` · T${held.id}`), 'the task on the folded line');
  });

  it('names no task after the turn when the agent holds none', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    const held = fx.task;
    fx.task = null;
    try {
      await mountWithTurn(s);
      s.daemon.chat.setWorking(ARCH, false);
      await until(() => box.querySelector('.ran'), 'the folded line');
      expect(box.querySelector('.ran')!.textContent).not.toMatch(/T\d/);
    } finally {
      fx.task = held;
    }
  });
});
