// The live line in an agent's chat while its session hands off.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
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

let root: Root | null = null;
let box: HTMLElement;
const card = () => box.querySelector<HTMLElement>('.card-handoff');

async function mount(s: ReturnType<typeof setup>) {
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
}
let keep = () => {};
afterEach(async () => {
  keep();
  keep = () => {};
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('handoff line', () => {
  it('says why and what while the session hands off', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = { handoff: { cause: 'context_full', phase: 'writing', since: new Date().toISOString() } };
    await mount(s);
    await until(() => card(), 'the line');
    expect(card()!.textContent).toBe('Restarting: context full. Writing the handoff…');
    expect(box.querySelector('[data-agent="handline.architect"] .restarting')!.textContent).toBe('Restarting…');
    expect(box.querySelector('[data-agent="handline.architect"] .row-line')!.textContent).toBe('Context full: writing the handoff');
    expect(card()!.querySelector('svg')!.getAttribute('class')).toContain('animate-');
    expect(box.querySelectorAll('.card-handoff')).toHaveLength(1); // earlier session boundaries stay above it
  });

  it('changes the words in place when the phase moves on', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = { handoff: { cause: 'context_full', phase: 'writing', since: new Date().toISOString() } };
    await mount(s);
    await until(() => card(), 'the line');
    fx.view = { handoff: { cause: 'context_full', phase: 'starting', since: new Date().toISOString() } };
    const session = s.daemon.sessions.get(ARCH)!;
    (s.daemon as unknown as { script: { setState(x: unknown, st: string): void } }).script.setState(session, session.state); // announces the change as the daemon does
    await until(() => card()?.textContent === 'Restarting: context full. Starting a new session…', 'the next phase');
    expect(box.querySelectorAll('.card-handoff')).toHaveLength(1);
  });

  it('drops the line once the new session line is in the chat, never showing both', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = { handoff: { cause: 'context_full', phase: 'starting', since: new Date(Date.now() - 2 * 3600_000).toISOString() } }; // before the seeded session line
    await mount(s);
    await until(() => box.querySelector('.msg, .card-session, [data-kind="session"]'), 'the chat');
    await wait(300);
    expect(card()).toBeNull();
  });

  it('shows nothing when no handoff runs', async () => {
    const s = setup();
    await mount(s);
    await wait(300);
    expect(card()).toBeNull();
  });
});
